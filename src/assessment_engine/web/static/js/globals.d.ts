// 표현계층 전역 타입 선언 (ambient) — base.html <script> 로 로드되는 UMD lib + 모듈 전역.
//
// 정석 gradual 채택: 컴파일러 강제의 핵심 가치는 fetch 경계(생성 api.ts 타입) 계약이다. 프로젝트
// 자체 전역(ChartUtils 등)은 여기서 실용적으로 선언하고, 각 모듈이 // @ts-check 로 자기 함수를
// 타입하며 점진적으로 정밀화한다. Chart/cytoscape 는 devDep 패키지 타입에서.

import type { Chart as ChartJs, ChartConfiguration } from "chart.js";
import type cytoscapeFn from "cytoscape";

/** chart-utils.js 가 window.ChartUtils 로 노출하는 공개 API (표시 파생 헬퍼 — 클라 재계산 아님).
 *  시그니처는 chart-utils.js 실제 구현 기준. 내부 표시 유틸이라 반환·복합 인자는 permissive(any). */
interface ChartUtilsApi {
  RANGE_LABEL: Record<string, string>;
  AUTO_BUCKET: Record<string, string>;
  BUCKET_LABEL: Record<string, string>;
  RANGE_MS: Record<string, number>;
  BUCKET_MS: Record<string, number>;
  COLORS: Record<string, string>;
  themeColor(): string;
  fmtKst(isoStr: string): string;
  fmtLabel(ts: string, range: string): string;
  fmtKbChart(v: number | null): string;
  getAnchorEnd(inputId: string): Date | null;
  initAnchor(inputId: string): void;
  makeBucketGrid(rangeKey: string, bucketKey: string, anchorEnd?: Date | null): number[];
  joinToGrid(grid: number[], rows: any[], bMs: number): Array<number | null>;
  buildDimDatasets(rows: any[], bMs: number, grid: number[], metaMap?: any, opts?: any): any[];
  fmtThroughput(kb: number | null): string;
  bindToggle(groupId: string, onChange: (val: string) => void): void;
  initAutoRefresh(onRefresh: () => void, intervalMs?: number): void;
  safeArray<T>(arr: T[] | null | undefined): T[];
  naWindows(osFamily: string | null, key: string, formatted: string): string;
  buildAvgMaxDatasets(avgRows: any[], maxRows: any[], bMs: number, grid: number[], opts?: any): any[];
  buildAvgMaxLegend(containerId: string, chart: any, opts?: any): any;
  renderChipLegend(container: any, chart: any): void;
}

interface TableUtilsApi {
  restripe(table: HTMLElement): void;
  sortByColumn(table: HTMLElement, colIndex: number): void;
  makeSortable(table: HTMLElement): void;
}

interface ToastUtilsApi {
  show(message: string, kind?: "pending" | "ok" | "err" | string): HTMLElement | null;
  dismissAll(): void;
}

interface EmitUtilsApi {
  submitNavigate(btn: HTMLElement | null, urlFn: () => string, opts?: any): Promise<void>;
}

interface TaskModalApi {
  open(taskId: string): void;
  close(): void;
  pollUntilFinal(taskId: string): void;
}

declare global {
  // UMD 전역 (vendor min.js — <script> 로 window 에 노출).
  const Chart: typeof ChartJs & { new (ctx: unknown, cfg: ChartConfiguration): ChartJs };
  const cytoscape: typeof cytoscapeFn;

  // 프로젝트 모듈 전역 (각 util 이 window.X 로 노출).
  const ChartUtils: ChartUtilsApi;
  const TableUtils: TableUtilsApi;
  const ToastUtils: ToastUtilsApi;
  const EmitUtils: EmitUtilsApi;
  const TaskModal: TaskModalApi;

  interface Window {
    ChartUtils: ChartUtilsApi;
    TableUtils: TableUtilsApi;
    ToastUtils: ToastUtilsApi;
    EmitUtils: EmitUtilsApi;
    TaskModal: TaskModalApi;
  }
}

export {};
