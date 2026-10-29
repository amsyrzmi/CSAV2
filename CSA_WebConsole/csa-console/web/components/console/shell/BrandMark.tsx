/**
 * The fourstacks mark drawn inline so it follows the theme: three stacks in the text colour
 * and the accent stack in the primary colour. The file in public/brand keeps its fixed navy
 * and blue for pages outside the themed shell (the sign-in card), and is low contrast on Night.
 */
export function BrandMark({ size = 32 }: { size?: number }) {
  return (
    <svg
      viewBox="0 0 48 52"
      width={size}
      height={Math.round((size * 52) / 48)}
      fill="none"
      aria-hidden="true"
      className="shrink-0 text-[color:var(--fs-text)]"
    >
      <path d="m4 38 20 9 20-9-7-4-13 6-13-6Z" fill="currentColor" />
      <path d="m4 28 20 9 20-9-7-4-13 6-13-6Z" fill="currentColor" />
      <path d="m4 18 20 9 20-9-7-4-13 6-13-6Z" fill="var(--fs-primary)" />
      <path d="M24 2 4 11l20 9 20-9Z" fill="currentColor" />
    </svg>
  );
}
