import { cn } from "@/shared/lib/utils"

interface LogoProps {
  variant?: "sidebar" | "header" | "login"
  className?: string
}

export const Logo = ({ variant = "sidebar", className }: LogoProps) => {
  const sizes = {
    sidebar: "h-8 w-8",
    header: "h-6 w-6",
    login: "h-10 w-10",
  }

  return (
    <img
      src="/logo.png"
      alt="Biotact Admin"
      className={cn(sizes[variant], "object-contain", className)}
    />
  )
}
