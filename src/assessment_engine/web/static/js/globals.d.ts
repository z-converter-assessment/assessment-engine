// vendor UMD 전역 타입 선언 — `<script>` 로 window 에 실리는 라이브러리 둘.
//
// 프로젝트 모듈은 여기 없다. ESM import 로 서로를 부르므로 tsc 가 구현에서 타입을 직접 추론한다.
// 손으로 미러링한 선언을 두면 구현과 어긋나도 tsc 가 통과시켜, 실제로 두 건이 그렇게 어긋나 있었다
// (`COLORS` 를 Record 로 적었지만 배열, `pollUntilFinal` 을 1인자로 적었지만 2인자).

import type { Chart as ChartJs, ChartConfiguration } from "chart.js";
import type cytoscapeFn from "cytoscape";

declare global {
  const Chart: typeof ChartJs & { new (ctx: unknown, cfg: ChartConfiguration): ChartJs };
  const cytoscape: typeof cytoscapeFn;
}

export {};
