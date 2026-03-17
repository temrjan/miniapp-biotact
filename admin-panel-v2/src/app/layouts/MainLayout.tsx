import { useState, type ReactNode } from "react"
import { Link, useLocation } from "react-router-dom"
import { motion, AnimatePresence } from "framer-motion"
import {
  LayoutDashboard,
  Package,
  Folder,
  ShoppingCart,
  Users,
  Settings,
  LogOut,
  Image,
  Moon,
  Sun,
  Menu,
  X,
  Contact,
} from "lucide-react"
import { Button } from "@/shared/components/ui/button"
import { useAuth, useLogout } from "@/features/auth/hooks/useAuth"
import { useTheme } from "@/app/providers/useTheme"
import { cn } from "@/shared/lib/utils"

interface MainLayoutProps {
  children: ReactNode
}

const navItems = [
  { href: "/dashboard", label: "Главная", icon: LayoutDashboard },
  { href: "/orders", label: "Заказы", icon: ShoppingCart },
  { href: "/products", label: "Товары", icon: Package },
  { href: "/categories", label: "Категории", icon: Folder },
  { href: "/banners", label: "Баннеры", icon: Image },
  { href: "/users", label: "Пользователи", icon: Users },
  { href: "/customers", label: "CRM", icon: Contact },
  { href: "/settings", label: "Настройки", icon: Settings },
]

interface SidebarContentProps {
  pathname: string
  onCloseSidebar: () => void
  onToggleTheme: () => void
  resolvedTheme: 'light' | 'dark'
  userName: string
  onLogout: () => void
  isLogoutPending: boolean
}

function SidebarContent({
  pathname,
  onCloseSidebar,
  onToggleTheme,
  resolvedTheme,
  userName,
  onLogout,
  isLogoutPending,
}: SidebarContentProps) {
  return (
    <>
      <div className="h-16 border-b flex items-center justify-between px-6">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-primary/10 rounded-lg flex items-center justify-center">
            <span className="text-primary font-bold">B</span>
          </div>
          <h1 className="text-xl font-bold">Biotact Admin</h1>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="lg:hidden"
          onClick={onCloseSidebar}
        >
          <X className="h-5 w-5" />
        </Button>
      </div>

      <nav className="flex-1 p-4 space-y-2">
        {navItems.map((item) => {
          const isActive = pathname === item.href ||
                          pathname.startsWith(item.href + "/")
          const Icon = item.icon

          return (
            <Link key={item.href} to={item.href} onClick={onCloseSidebar}>
              <Button
                variant={isActive ? "secondary" : "ghost"}
                className={cn(
                  "w-full justify-start",
                  isActive && "bg-primary/10 text-primary hover:bg-primary/20"
                )}
              >
                <Icon className="mr-2 h-4 w-4" />
                {item.label}
              </Button>
            </Link>
          )
        })}
      </nav>

      <div className="p-4 border-t space-y-3">
        {/* Theme Toggle */}
        <Button
          variant="outline"
          className="w-full justify-start"
          onClick={onToggleTheme}
        >
          {resolvedTheme === 'dark' ? (
            <>
              <Sun className="mr-2 h-4 w-4" />
              Светлая тема
            </>
          ) : (
            <>
              <Moon className="mr-2 h-4 w-4" />
              Тёмная тема
            </>
          )}
        </Button>

        {/* User Info */}
        <div className="text-sm">
          <div className="font-medium">{userName}</div>
          <div className="text-xs text-muted-foreground">Администратор</div>
        </div>

        {/* Logout */}
        <Button
          variant="outline"
          className="w-full justify-start text-destructive hover:text-destructive"
          onClick={onLogout}
          disabled={isLogoutPending}
        >
          <LogOut className="mr-2 h-4 w-4" />
          Выйти
        </Button>
      </div>
    </>
  )
}

export const MainLayout = ({ children }: MainLayoutProps) => {
  const { user } = useAuth()
  const logout = useLogout()
  const location = useLocation()
  const { setTheme, resolvedTheme } = useTheme()
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const toggleTheme = () => {
    setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')
  }

  const closeSidebar = () => setSidebarOpen(false)
  const userName = user?.first_name || user?.username || "Admin"

  const sidebarProps: SidebarContentProps = {
    pathname: location.pathname,
    onCloseSidebar: closeSidebar,
    onToggleTheme: toggleTheme,
    resolvedTheme,
    userName,
    onLogout: () => logout.mutate(),
    isLogoutPending: logout.isPending,
  }

  return (
    <div className="min-h-screen bg-background flex">
      {/* Desktop Sidebar */}
      <aside className="hidden lg:flex w-64 border-r bg-card flex-col">
        <SidebarContent {...sidebarProps} />
      </aside>

      {/* Mobile Sidebar Overlay */}
      <AnimatePresence>
        {sidebarOpen && (
          <>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 bg-black/50 z-40 lg:hidden"
              onClick={closeSidebar}
            />
            <motion.aside
              initial={{ x: -280 }}
              animate={{ x: 0 }}
              exit={{ x: -280 }}
              transition={{ type: "spring", damping: 25, stiffness: 200 }}
              className="fixed left-0 top-0 bottom-0 w-64 border-r bg-card flex flex-col z-50 lg:hidden"
            >
              <SidebarContent {...sidebarProps} />
            </motion.aside>
          </>
        )}
      </AnimatePresence>

      {/* Main Content */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Mobile Header */}
        <header className="lg:hidden h-16 border-b bg-card flex items-center px-4 gap-4">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setSidebarOpen(true)}
          >
            <Menu className="h-5 w-5" />
          </Button>
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 bg-primary/10 rounded flex items-center justify-center">
              <span className="text-primary font-bold text-sm">B</span>
            </div>
            <span className="font-semibold">Biotact Admin</span>
          </div>
        </header>

        {/* Page Content */}
        <main className="flex-1 overflow-auto">{children}</main>
      </div>
    </div>
  )
}
