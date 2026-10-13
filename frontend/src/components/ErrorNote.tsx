export function ErrorNote({ message }: { message: string }) {
  return (
    <div role="alert" className="whitespace-pre-line rounded-2xl border border-red-900/30 bg-red-100 p-4 text-sm font-medium leading-6 text-red-950 shadow-sm">
      {message}
    </div>
  );
}
