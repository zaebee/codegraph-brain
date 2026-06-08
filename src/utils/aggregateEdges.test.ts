import { describe, it, expect } from "vitest";
import { aggregateEdges } from "./aggregateEdges";
import type { Edge } from "@xyflow/react";

function makeEdge(id: string, source: string, target: string, edgeType: string): Edge {
  return {
    id,
    source,
    target,
    style: { stroke: "#4fc3f7", strokeWidth: 1.5, opacity: 0.8 },
    data: { edgeType },
  } as Edge;
}

describe("aggregateEdges", () => {
  it("returns single edges unchanged", () => {
    const edges = [makeEdge("e1", "a", "b", "CALLS")];
    const result = aggregateEdges(edges);
    expect(result).toEqual(edges);
  });

  it("aggregates multiple CALLS between same source/target", () => {
    const edges = [
      makeEdge("e1", "a", "b", "CALLS"),
      makeEdge("e2", "a", "b", "CALLS"),
      makeEdge("e3", "a", "b", "CALLS"),
    ];
    const result = aggregateEdges(edges);
    expect(result).toHaveLength(1);
    expect(result[0].label).toBe("×3");
    expect(result[0].data?.isAggregated).toBe(true);
    expect(result[0].data?.aggregatedCount).toBe(3);
    expect(result[0].data?.aggregatedEdgeIds).toEqual(["e1", "e2", "e3"]);
  });

  it("sets strokeWidth based on sqrt(count)", () => {
    const edges = [
      makeEdge("e1", "a", "b", "CALLS"),
      makeEdge("e2", "a", "b", "CALLS"),
    ];
    const result = aggregateEdges(edges);
    expect(result[0].style?.strokeWidth).toBeCloseTo(Math.sqrt(2) * 1.5);
  });

  it("does not aggregate non-CALLS edge types by default", () => {
    const edges = [
      makeEdge("e1", "a", "b", "IMPORTS"),
      makeEdge("e2", "a", "b", "IMPORTS"),
    ];
    const result = aggregateEdges(edges);
    expect(result).toHaveLength(2);
  });

  it("aggregates specified types when provided", () => {
    const edges = [
      makeEdge("e1", "a", "b", "IMPORTS"),
      makeEdge("e2", "a", "b", "IMPORTS"),
    ];
    const result = aggregateEdges(edges, ["IMPORTS"]);
    expect(result).toHaveLength(1);
    expect(result[0].label).toBe("×2");
  });

  it("handles mixed: aggregates duplicates, keeps singles", () => {
    const edges = [
      makeEdge("e1", "a", "b", "CALLS"),
      makeEdge("e2", "a", "b", "CALLS"),
      makeEdge("e3", "a", "c", "CALLS"),
    ];
    const result = aggregateEdges(edges);
    expect(result).toHaveLength(2);
    const agg = result.find((e) => e.data?.isAggregated);
    expect(agg?.data?.aggregatedCount).toBe(2);
    const single = result.find((e) => !e.data?.isAggregated);
    expect(single?.id).toBe("e3");
  });

  it("handles empty edges", () => {
    const result = aggregateEdges([]);
    expect(result).toEqual([]);
  });
});
