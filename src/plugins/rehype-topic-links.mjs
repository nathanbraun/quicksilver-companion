/**
 * Rehype plugin that converts /topic/* links to plain text
 * when the target topic page doesn't exist.
 */
import { readdirSync } from 'fs';
import { join } from 'path';
import { visit } from 'unist-util-visit';

// Build set of existing topic slugs at module load time
const topicsDir = join(import.meta.dirname, '../content/topics');
const existingSlugs = new Set(
  readdirSync(topicsDir)
    .filter(f => f.endsWith('.md'))
    .map(f => f.replace('.md', ''))
);

export function rehypeTopicLinks() {
  return (tree) => {
    visit(tree, 'element', (node, index, parent) => {
      if (
        node.tagName === 'a' &&
        node.properties?.href &&
        typeof node.properties.href === 'string'
      ) {
        const match = node.properties.href.match(/^\/topic\/([a-z0-9-]+)$/);
        if (match && !existingSlugs.has(match[1])) {
          // Replace <a> with a <span> containing the same children
          node.tagName = 'span';
          delete node.properties.href;
        }
      }
    });
  };
}
