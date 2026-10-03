/**
 * An image in an employee's reply. Replies are model output, and a reply
 * steered by something the employee read could embed an image whose URL
 * carries data out to another server the moment it loads. So only images
 * served by this app's own workspace route load by themselves; any other
 * address waits behind a button that names its host, and loads without a
 * referrer once the owner asks for it.
 */

import { useState, type ImgHTMLAttributes } from 'react';
import { ImageOff } from 'lucide-react';

import { Button } from '@/components/ui/button';

import { imageHost, isWorkspaceImage } from './workspaceImage';

type SafeImageProps = ImgHTMLAttributes<HTMLImageElement> & { node?: unknown };

export function SafeImage({ node: _node, src, alt, ...rest }: SafeImageProps) {
  const source = typeof src === 'string' ? src : undefined;
  const [allowed, setAllowed] = useState(() => isWorkspaceImage(source));

  if (!source) return null;
  if (allowed) {
    return <img {...rest} src={source} alt={alt ?? ''} loading="lazy" referrerPolicy="no-referrer" />;
  }
  return (
    <Button type="button" variant="chip" size="chip" onClick={() => setAllowed(true)} title={source}>
      <ImageOff aria-hidden className="size-3.5" />
      {`Show image from ${imageHost(source)}`}
    </Button>
  );
}
