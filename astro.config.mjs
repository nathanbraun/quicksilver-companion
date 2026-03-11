// @ts-check
import { defineConfig } from 'astro/config';
import mdx from '@astrojs/mdx';
import { rehypeTopicLinks } from './src/plugins/rehype-topic-links.mjs';

export default defineConfig({
  integrations: [mdx()],
  site: 'https://v2.quicksilver.wiki',
  markdown: {
    rehypePlugins: [rehypeTopicLinks],
  },
});
