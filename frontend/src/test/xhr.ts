import { vi } from 'vitest';

// XMLHttpRequest as the test drives it, for the uploads that report their progress
// (putFileWithProgress in src/api/client.ts): it records what was sent and lets the test report
// progress, answer or fail. jsdom's own would try the network.
export class FakeXhr extends EventTarget {
  static sent: FakeXhr[] = [];

  readonly upload = new EventTarget();
  method = '';
  url = '';
  headers: Record<string, string> = {};
  body: unknown = null;
  status = 0;
  responseText = '';
  aborted = false;
  private responseHeaders: Record<string, string> = {};

  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }

  setRequestHeader(name: string, value: string) {
    this.headers[name] = value;
  }

  getResponseHeader(name: string): string | null {
    return this.responseHeaders[name] ?? null;
  }

  send(body: unknown) {
    this.body = body;
    FakeXhr.sent.push(this);
  }

  abort() {
    this.aborted = true;
    this.dispatchEvent(new Event('abort'));
  }

  // The browser has sent `loaded` of `total` bytes.
  progress(loaded: number, total: number) {
    // A plain Event with ProgressEvent's fields: Node has no ProgressEvent.
    this.upload.dispatchEvent(Object.assign(new Event('progress'), { loaded, total, lengthComputable: true }));
  }

  // The API's answer.
  respond(status: number, body: unknown, headers: Record<string, string> = {}) {
    this.status = status;
    this.responseText = body === undefined ? '' : JSON.stringify(body);
    this.responseHeaders = headers;
    this.dispatchEvent(new Event('load'));
  }

  // No answer at all: the connection broke.
  fail() {
    this.dispatchEvent(new Event('error'));
  }
}

// Stubs XMLHttpRequest with FakeXhr; vi.unstubAllGlobals() takes it back. Returns the requests
// sent, the latest last.
export function stubXhr(): FakeXhr[] {
  FakeXhr.sent = [];
  vi.stubGlobal('XMLHttpRequest', FakeXhr);
  return FakeXhr.sent;
}
