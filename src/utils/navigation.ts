import { getCollection } from 'astro:content';

export async function getAnnotationPages(): Promise<number[]> {
  const annotations = await getCollection('annotations');
  const pages = [...new Set(annotations.map(a => a.data.page))];
  return pages.sort((a, b) => a - b);
}

export async function getPrevNextPages(currentPage: number): Promise<{ prev?: number; next?: number }> {
  const pages = await getAnnotationPages();
  const idx = pages.indexOf(currentPage);
  return {
    prev: idx > 0 ? pages[idx - 1] : undefined,
    next: idx < pages.length - 1 ? pages[idx + 1] : undefined,
  };
}
