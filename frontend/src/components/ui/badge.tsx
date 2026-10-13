import * as React from "react";

import { cn } from "../../lib/utils";

// A tilted sticker: `.sticker` in index.css.
export function Badge({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("sticker", className)} {...props} />;
}
