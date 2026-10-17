function double(x: number): number {
  return 2 * x;
}

export function doubled(xs: number[]): number[] {
  return xs.map(double);
}
