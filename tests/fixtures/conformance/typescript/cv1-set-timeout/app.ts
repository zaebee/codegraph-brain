function tick(): void {}

export function schedule(): void {
  setTimeout(tick, 10);
}
