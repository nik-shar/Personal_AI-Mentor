/**
 * Minimal ambient typings for `vis-network` (the package ships no .d.ts).
 * We only use Network construction + the click handler + destroy.
 */
declare module "vis-network" {
  export interface NetworkOptions {
    autoResize?: boolean;
    physics?: Record<string, unknown>;
    interaction?: Record<string, unknown>;
    layout?: Record<string, unknown>;
    [key: string]: unknown;
  }

  export interface NetworkData {
    nodes?: unknown[];
    edges?: unknown[];
  }

  export interface ClickParams {
    nodes: string[];
    edges: string[];
    [key: string]: unknown;
  }

  export class Network {
    constructor(container: HTMLElement, data: NetworkData, options?: NetworkOptions);
    on(event: string, callback: (params: ClickParams) => void): void;
    once(event: string, callback: (params: ClickParams) => void): void;
    off(event: string): void;
    destroy(): void;
    redraw(): void;
    fit(options?: Record<string, unknown>): void;
  }

  export class DataSet<T = unknown> {
    constructor(data?: T[]);
    add(data: T | T[]): void;
    get(ids?: unknown): T[];
    clear(): void;
  }
}
