import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';

const annotations = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/annotations' }),
  schema: z.object({
    title: z.string(),
    page: z.number(),
    quote: z.string().optional(),
    book: z.number(), // 1=Quicksilver, 2=King of the Vagabonds, 3=Odalisque
    book_title: z.string(),
    chapter_start_page: z.number(),
    chapter_location: z.string(),
    chapter_date: z.string(),
    characters: z.array(z.string()).default([]),
    topics: z.array(z.string()).default([]),
    original_authors: z.array(z.string()).default([]),
  }),
});

const topics = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/topics' }),
  schema: z.object({
    title: z.string(),
    category: z.string(),
    fictional: z.boolean().optional(),
    first_mention_page: z.number().optional(),
    related_characters: z.array(z.string()).default([]),
  }),
});

export const collections = { annotations, topics };
