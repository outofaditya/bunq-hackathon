import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  // Base — Press-Kit-flat with a small lift on hover, no gradient panning,
  // boxy radius. Springs back on active for tactile feedback.
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded font-semibold " +
    "transition-[transform,background-color,box-shadow,border-color,color] duration-200 ease-[cubic-bezier(0.2,0,0,1)] " +
    "hover:-translate-y-[1px] active:translate-y-0 active:scale-[0.97] " +
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background " +
    "disabled:pointer-events-none disabled:opacity-50 disabled:hover:translate-y-0 " +
    "[&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0 [&_svg]:transition-transform [&_svg]:duration-200 hover:[&_svg]:scale-110",
  {
    variants: {
      variant: {
        // bunq Orange CTA — solid, white text, brightens to orange-deep on hover
        default: [
          "bg-bunq-orange text-bunq-white",
          "shadow-[0_8px_24px_-8px_rgba(255,120,25,0.45)]",
          "hover:bg-bunq-orange-deep hover:shadow-[0_14px_34px_-8px_rgba(255,120,25,0.65)]",
        ].join(" "),
        destructive: [
          "bg-bunq-red text-bunq-white",
          "shadow-[0_6px_20px_-8px_rgba(230,50,35,0.55)]",
          "hover:opacity-95 hover:shadow-[0_12px_30px_-8px_rgba(230,50,35,0.75)]",
        ].join(" "),
        outline: [
          "border border-border bg-card text-card-foreground",
          "hover:border-bunq-orange/60 hover:bg-paper-850 hover:text-foreground",
        ].join(" "),
        secondary: [
          "bg-secondary text-secondary-foreground border border-border",
          "hover:bg-paper-850 hover:border-paper-700",
        ].join(" "),
        ghost: "text-foreground hover:bg-accent hover:text-accent-foreground",
        link: [
          "text-bunq-orange underline-offset-4",
          "hover:underline hover:text-bunq-orange-deep",
        ].join(" "),
        // Kept for back-compat with the IdleMic — same as default
        glow: [
          "bg-bunq-orange text-bunq-white",
          "shadow-[0_10px_36px_-8px_rgba(255,120,25,0.55)]",
          "hover:bg-bunq-orange-deep hover:shadow-[0_18px_44px_-8px_rgba(255,120,25,0.7)]",
        ].join(" "),
      },
      size: {
        default: "h-9 px-4 py-2 text-body",
        sm: "h-8 rounded px-3 text-meta",
        lg: "h-11 rounded-md px-6 text-body",
        icon: "h-9 w-9",
        xl: "h-20 w-20 rounded-full",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";

export { Button, buttonVariants };
