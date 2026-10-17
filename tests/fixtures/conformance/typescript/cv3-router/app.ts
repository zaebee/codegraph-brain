declare const router: { get(path: string, h: () => void): void };

function index(): void {}

export function routes(): void {
  router.get("/", index);
}
