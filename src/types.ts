export type NodeType = "FILE" | "CLASS" | "FUNCTION" | "METHOD";
export type NodeNamespace = "INTERNAL" | "STDLIB" | "EXTERNAL" | "UNKNOWN";

export interface GraphNode {
  id: string;
  type: NodeType;
  name: string;
  file_path: string;
  start_line: number;
  end_line: number;
  language: string;
  namespace: NodeNamespace;
  ontology_class: string | null;
  domains: string[];
  confidence_score: number;
  metadata: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  weight?: number;
  confidence?: number;
  context?: string;
  file_path?: string;
  line_number?: number;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}
