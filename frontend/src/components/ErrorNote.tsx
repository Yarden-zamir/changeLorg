export function ErrorNote({ message }: { message: string }) {
  return (
    <div role="alert" className="whitespace-pre-line rounded border-2 border-l-8 border-line border-l-error bg-error-surface p-4 text-sm font-semibold leading-6 text-ink">
      {message}
    </div>
  );
}
