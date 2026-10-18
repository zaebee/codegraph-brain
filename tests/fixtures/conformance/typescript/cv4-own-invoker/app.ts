function measure(name: string, fn: () => void): void {
  fn();
}

function work(): void {}

export function run(): void {
  measure("work", work);
}
