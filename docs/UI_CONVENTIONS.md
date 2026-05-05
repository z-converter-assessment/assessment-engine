# UI 설계 규약

템플릿(`web/templates/`) 작업 시 지켜야 할 설계 결정과 패턴을 기록한다.

---

## 1. 헤더 네비게이션

- `base.html` 헤더의 브랜드(`ZConverter Assessment`)가 `/servers/`로 이동하는 `<a>` 태그.
- 별도 "서버 목록" nav 링크 없음 — 브랜드 클릭으로 대체.

```html
<a href="/servers/" class="brand">ZConverter Assessment</a>
```

---

## 2. SSE 상태 + 수집기준시간 레이아웃

SSE dot/label과 수집기준시간 span은 반드시 **단일 flex 컨테이너** 안에 둔다.
두 요소를 별도 div로 분리하면 컨테이너 폭에 따라 줄바꿈이 발생한다.

```html
<div id="sse-status" class="no-print"
     style="display:flex; align-items:center; gap:5px; font-size:11px; color:#94a3b8; white-space:nowrap;">
  <span id="sse-dot" class="dot dot-off"></span>
  <span id="sse-label">연결 중...</span>
  <span id="xxx-snapshot-ts" style="margin-left:4px;"></span>
</div>
```

수집기준시간 갱신:
```javascript
if (data.collected_at)
  document.getElementById('xxx-snapshot-ts').textContent = '수집 기준: ' + fmtKst(data.collected_at);
```

`fmtKst` 함수는 각 템플릿에 반드시 정의되어야 한다 (누락 시 무음 실패).

---

## 3. Chart.js 공통 패턴

라이브러리: **Chart.js 4.4.3** (`https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js`)

### 3-1. avg+max 음영 패턴

avg 데이터셋(짝수 인덱스)과 max ghost 데이터셋(홀수 인덱스)을 쌍으로 구성한다.

```javascript
// avg 데이터셋
{
  label: dim,
  data: grid.map(t => avgMap[t] ?? null),
  borderColor: color,
  backgroundColor: color + '28',
  borderWidth: 2,
  pointRadius: 1,
  pointHoverRadius: 3,
  tension: 0.3,
  fill: '+1',          // 다음 데이터셋(max)까지 음영
  spanGaps: false,
}

// max ghost 데이터셋
// realData: 실제 max 값 (툴팁에서 참조)
// bufferedMaxData: avg가 null인 버킷은 null — 빈 구간 음영 방지
const realMaxData     = grid.map(t => maxMap[t] ?? null);
const bufferedMaxData = grid.map(t => {
  if (avgMap[t] == null) return null;
  return maxMap[t] ?? avgMap[t];
});
{
  label: dim + '__max',
  data: bufferedMaxData,
  realData: realMaxData,
  borderColor: 'transparent',
  backgroundColor: 'transparent',
  borderWidth: 0,
  pointRadius: 0,
  pointHoverRadius: 0,
  tension: 0.3,
  fill: false,
  spanGaps: false,
}
```

툴팁 설정:
```javascript
plugins: {
  tooltip: {
    filter: item => item.datasetIndex % 2 === 0,   // avg만 표시
    callbacks: {
      label: ctx => {
        const avg    = ctx.parsed.y;
        const maxDs  = chart?.data.datasets[ctx.datasetIndex + 1];
        const realMax = maxDs?.realData?.[ctx.dataIndex];
        if (realMax != null)
          return ` ${ctx.dataset.label}: 평균 ${fmt(avg)} / 최대 ${fmt(realMax)}`;
        return ` ${ctx.dataset.label}: ${fmt(avg)}`;
      }
    }
  }
}
```

### 3-2. 포인트 크기 규칙

| 데이터셋 종류 | pointRadius | pointHoverRadius |
|--------------|-------------|-----------------|
| avg (실데이터) | 1 | 3 |
| max ghost | 0 | 0 |

모든 차트 템플릿에 동일하게 적용. `pointRadius: 0` (완전 숨김)이나 `pointRadius: 3+` (과도하게 눈에 띔)은 사용하지 않는다.

### 3-3. suggestedMax 상수화

Y축 기본 기준선은 스크립트 상단에 명명 상수로 분리한다.

```javascript
const NET_Y_SUGGESTED_MAX = 2048; // B/s 기본 기준선 (≈2 kB/s). 조정 가능.
```

- `suggestedMax`: soft ceiling. 실데이터가 초과하면 자동 확장.
- `max`: hard ceiling. 데이터가 잘리더라도 고정 상한. 사용 지양.

---

## 4. Y축 설계별 결정

### 네트워크 I/O

| 항목 | 값 |
|------|-----|
| 데이터 단위 | B/s 원값 그대로 (나누기 없음) |
| 포매터 | `fmtKbChart(v)` — 값에 따라 자동으로 B/s / kB/s / MB/s 선택 |
| Y축 title | `'처리량'` (단위가 동적이므로 고정 단위명 부적합) |
| 기준선 | `NET_Y_SUGGESTED_MAX = 2048` (≈2 kB/s) |

```javascript
function fmtKbChart(v) {
  if (v == null) return '';
  if (v >= 1024 * 1024) return (v / 1024 / 1024).toFixed(1) + ' MB/s';
  if (v >= 1024)        return (v / 1024).toFixed(1) + ' kB/s';
  return v.toFixed(0) + ' B/s';
}
```

네트워크 스냅샷 테이블용 `fmtKbps(v)`와 혼동 주의 — 테이블은 kBps 입력, 차트는 B/s 입력.

### 메모리/CPU 사용률

- `beginAtZero: true` + auto scale
- `max: 100` 고정 사용 안 함 — 개별 컴포넌트 추이 가시성을 위해 자동 스케일

### 스왑 사용률

- `beginAtZero: true, suggestedMax: 25`
- 사용률이 낮은 환경에서도 최소 0–25% 범위를 보여 추이 파악 가능

### IOPS (디스크 I/O)

- `ticks: { precision: 0 }` — 정수 눈금만 표시
- `stepSize: 1` + 정수 callback 조합 불필요

---

## 5. comp 차트 (도넛 / 수평바) 생명주기

`loadCompChart` → `renderCompChart` 패턴에서 차트 destroy는 `renderCompChart` 내부에서만 수행한다.
`loadCompChart`에서 `chart.destroy()`를 먼저 호출하면 `renderCompChart`의 update 분기(재사용)가 항상 dead code가 된다.

```javascript
function renderCompChart(data) {
  if (compChart) {
    compChart.data = newData;
    compChart.update();
    return;          // 재사용
  }
  compChart = new Chart(...);  // 최초 생성
}

async function loadCompChart() {
  const data = await fetch(...).then(r => r.json());
  renderCompChart(data);       // destroy 없음
}
```

---

## 6. CPU 상세 페이지 구성

- **정적 정보**: `cpu_model`, `cpu_cores`를 `<dl class="kv">` 블록으로 SSR 렌더링. 동적 메트릭보다 위에 표시.
- **코어당 포화 기준**: Load `{cpu_cores}.0 = 100%` — Load Average의 절대값 해석 맥락 제공.
- **피크탐지 로직 설명**: 평균/최대/p95 각각의 용도를 `<ul><li>` 목록으로 표시.
- **토글 분리**: agg 토글 그룹(평균/최대/p95)과 기간 토글 그룹 사이에 `1px` 수직 구분선 추가.

```html
<div style="width:1px; height:22px; background:#e2e8f0; margin:0 8px;" class="no-print"></div>
```

---

## 7. 추후 리팩토링 여지 (로직 변경 없음)

현재 템플릿 JS는 전부 인라인 `<script>` 블록으로 작성되어 있다.
아래 중복 코드들을 `web/static/js/chart-utils.js`로 추출하면 유지보수성이 높아진다.

**전제 조건**: FastAPI에 `StaticFiles` 마운트 추가 필요.
```python
app.mount("/static", StaticFiles(directory="web/static"), name="static")
```

### 중복 포인트

| 항목 | 현재 상태 | 해당 파일 |
|------|----------|----------|
| `fmtKst()` | 각 파일에 동일 구현 반복 | network, memory, cpu, storage |
| `bindToggle()` / `bindToggleGroup()` | 각 파일에 동일 구현 반복 | network, memory, cpu, storage, chart |
| `COLORS`, `AUTO_BUCKET`, `BUCKET_MS` | 각 파일에 동일 상수 반복 | network, memory, cpu, storage |
| `makeBucketGrid()` | 각 파일에 동일 구현 반복 | network, memory, storage |
| avg+max ghost dataset 빌드 패턴 | `avgMap/maxMap → bufferedMaxData → datasets.push` 패턴 반복 | network, memory, storage |
| SSE 초기화 블록 | `onopen/onerror` dot 토글 + label 업데이트 패턴 반복 | network, memory, cpu |