import * as React from "react";

import { cn } from "../../lib/utils";

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "secondary";
};

// One button style for the app: `.editor-button` in index.css.
export function Button({ className, variant = "default", ...props }: ButtonProps) {
  return (
    <button
      className={cn(
        "editor-button inline-flex h-10 items-center justify-center px-4 py-2 text-sm",
        variant === "default" && "editor-primary",
        className,
      )}
      {...props}
    />
  );
}
