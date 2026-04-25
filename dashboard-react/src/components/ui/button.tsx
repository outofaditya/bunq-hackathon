import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  // Base — every button gets the spring-y hover/active behaviour, focus ring,
  // and an SVG sizer. Specific variants layer colour + shadow on top.
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded font-medium " +
    "transition-[transform,background-color,box-shadow,border-color,color] duration-200 ease-[cubic-bezier(0.2,0,0,1)] " +
    "hover:-translate-y-[1px] active:translate-y-0 active:scale-[0.97] " +
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background " +
    "disabled:pointer-events-none disabled:opacity-50 disabled:hover:translate-y-0 " +
    "[&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0 [&_svg]:transition-transform [&_svg]:duration-200 hover:[&_svg]:scale-110",
  {
    variants: {
      variant: {
        default: [
          "text-primary-foreground",
          "bg-[image:var(--gradient-warm)] bg-[length:200%_200%] bg-[position:0%_50%]",
          "shadow-[0_8px_24px_-8px_rgba(255,111,143,0.4)]",
          "hover:bg-[position:100%_50%] hover:shadow-[0_14px_34px_-8px_rgba(255,111,143,0.55)]",
        ].join(" "),
        destructive: [
          "bg-destructive text-destructive-foreground",
          "shadow-[0_6px_20px_-8px_rgba(255,111,143,0.5)]",
          "hover:bg-destructive/90 hover:shadow-[0_12px_30px_-8px_rgba(255,111,143,0.65)]",
        ].join(" "),
        outline: [
          "border border-border bg-card/60 text-card-foreground backdrop-blur",
          "hover:border-accent-coral/50 hover:bg-accent hover:shadow-[0_8px_24px_-12px_rgba(183,136,255,0.4)]",
        ].join(" "),
        secondary: [
          "bg-secondary text-secondary-foreground",
          "hover:bg-secondary/80 hover:shadow-[0_6px_18px_-10px_rgba(183,136,255,0.5)]",
        ].join(" "),
        ghost: "text-foreground hover:bg-accent hover:text-accent-foreground",
        link: [
          "text-primary underline-offset-4",
          "hover:underline hover:text-accent-coral",
        ].join(" "),
        glow: [
          "text-primary-foreground",
          "bg-[image:var(--gradient-hero)] bg-[length:300%_300%] bg-[position:0%_50%]",
          "shadow-[0_10px_36px_-8px_rgba(183,136,255,0.55)]",
          "hover:bg-[position:100%_50%] hover:shadow-[0_18px_44px_-8px_rgba(255,111,143,0.7)]",
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
