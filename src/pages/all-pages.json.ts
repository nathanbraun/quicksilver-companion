import type { APIRoute } from 'astro';
import { getCollection } from 'astro:content';

export const GET: APIRoute = async () => {
  const annotations = await getCollection('annotations');
  const topics = await getCollection('topics');

  const pages: string[] = [
    '/',
    '/about',
    '/topics',
    '/before-you-read',
    '/before-book-2',
    '/before-book-3',
    '/book/1',
    '/book/2',
    '/book/3',
    '/offline',
  ];

  // Annotation pages
  const annotationPages = [...new Set(annotations.map(a => a.data.page))];
  for (const page of annotationPages) {
    pages.push(`/page/${page}`);
  }

  // Topic pages
  for (const topic of topics) {
    pages.push(`/topic/${topic.id}`);
  }

  return new Response(JSON.stringify(pages), {
    headers: { 'Content-Type': 'application/json' },
  });
};
