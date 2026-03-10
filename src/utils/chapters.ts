import chaptersData from '../data/chapters.json';

export interface Chapter {
  page: number;
  book: number;
  book_title: string;
  location: string;
  date: string;
}

export const chapters: Chapter[] = chaptersData as Chapter[];

export function getChapterForPage(page: number): Chapter | undefined {
  let result: Chapter | undefined;
  for (const ch of chapters) {
    if (ch.page <= page) {
      result = ch;
    } else {
      break;
    }
  }
  return result;
}

export function getChaptersByBook(book: number): Chapter[] {
  return chapters.filter(ch => ch.book === book);
}

export function getNextChapterPage(currentPage: number): number | undefined {
  for (const ch of chapters) {
    if (ch.page > currentPage) return ch.page;
  }
  return undefined;
}
