/**
 * @typedef {'FUNCTION' | 'CLASS' | 'METHOD' | 'EXTERNAL'} NodeType
 *
 * @typedef {Object} GraphNode
 * @property {string} id
 * @property {NodeType} type
 * @property {string} name
 * @property {string} file_path
 * @property {number} start_line
 * @property {number} end_line
 * @property {string} language
 * @property {string|null} ontology_class
 * @property {string[]} domains
 * @property {number} confidence_score
 * @property {Object} metadata
 *
 * @typedef {Object} GraphEdge
 * @property {string} id
 * @property {string} source
 * @property {string} target
 * @property {string} type
 * @property {number} [weight]
 * @property {number} [confidence]
 * @property {string} [context]
 * @property {string} [file_path]
 * @property {number} [line_number]
 *
 * @typedef {Object} GraphData
 * @property {GraphNode[]} nodes
 * @property {GraphEdge[]} edges
 */

export {};
