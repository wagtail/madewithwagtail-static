import type { ImageMetadata } from 'astro';

// All images colocated with the developer and site Markdown entries. Eager so
// lookups are synchronous: the glob only loads image metadata, not the files.
const images = import.meta.glob<{ default: ImageMetadata }>('/src/content/developers/**/*.webp', {
  eager: true,
});

// Indexed by lowercased path: image file names can differ from the entry IDs
// they belong to in case (e.g. the curo logo file is Curo.max-120x120.webp).
const imageIndex = new Map(
  Object.entries(images).map(([path, mod]) => [path.toLowerCase(), mod.default]),
);

// Resolves the image colocated with a developer or site entry, or null when
// the entry has none:
// - developer ID '<dev>' -> /src/content/developers/<dev>/<dev><suffix>
// - site ID '<dev>/<site>' -> /src/content/developers/<dev>/<site>/<site><suffix>
export function lookupImage(id: string, suffix: string): ImageMetadata | null {
  const fileName = `${id.split('/').at(-1)}${suffix}`;
  return imageIndex.get(`/src/content/developers/${id}/${fileName}`.toLowerCase()) ?? null;
}
