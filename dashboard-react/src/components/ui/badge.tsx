import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-sm px-2 py-0.5 text-meta font-medium transition-colors",
  {
    variants: {
      variant: {
        default:    "bg-secondary text-secondary-foreground",
        outline:    "border border-border text-foreground",
        complete:   "border border-status-complete/40 text-status-complete bg-status-complete/10",
        upcoming:   "border border-status-upcoming/40 text-status-upcoming bg-status-upcoming/10",
        overdue:    "border border-status-overdue/40 text-status-overdue bg-status-overdue/10",
        scheduled:  "border border-status-scheduled/40 text-status-scheduled bg-status-scheduled/10",
        priority:   "border border-status-priority/40 text-status-priority bg-status-priority/10",
      },
    },
    defaultVariants: { variant: "default" },
  }
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
