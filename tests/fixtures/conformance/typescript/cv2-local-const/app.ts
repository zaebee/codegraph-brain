function handle(): void {}

export function run(): void {
  const cb = handle;
  cb();
}
