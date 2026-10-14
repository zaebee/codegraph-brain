import { randomUUID } from "node:crypto";
import debounce from "lodash.debounce";
import { useState } from "react";
import { libFn } from "@x/lib";
import { helper } from "@app/util";

function setTimeout(fn: () => void): void {
  fn();
}

export function main(): number {
  useState(0);
  randomUUID();
  setTimeout(() => debounce(() => helper(libFn()), 10));
  return libFn();
}
