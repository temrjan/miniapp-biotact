"use client"

import { useState } from "react"
import { motion, AnimatePresence, LayoutGroup, type PanInfo } from "framer-motion"
import { cn } from "@utils/cn"
import { Grid3X3, Layers, LayoutList, Activity, Package, Zap, Gift, Box } from "lucide-react"
import { useHaptic } from "@hooks/useTelegram"
import type { Category } from "@/types"

export type LayoutMode = "stack" | "grid" | "list"

export interface CategoryCardStackProps {
  categories: Category[]
  className?: string
  defaultLayout?: LayoutMode
  selectedCategoryId?: string | null
  onCategorySelect?: (categoryId: string | null) => void
}

const layoutIcons = {
  stack: Layers,
  grid: Grid3X3,
  list: LayoutList,
}

// Иконки для категорий по slug или id
const categoryIcons: Record<string, React.ElementType> = {
  "active-life": Activity,
  "product": Package,
  "tehnika": Zap,
  "vitaminy-set": Gift,
  // fallback по id
  "6": Activity,
  "3": Package,
  "5": Zap,
  "4": Gift,
}

const SWIPE_THRESHOLD = 50

export function CategoryCardStack({
  categories,
  className,
  defaultLayout = "grid",
  selectedCategoryId = null,
  onCategorySelect,
}: CategoryCardStackProps) {
  const haptic = useHaptic()
  const [layout, setLayout] = useState<LayoutMode>(defaultLayout)
  const [activeIndex, setActiveIndex] = useState(0)
  const [isDragging, setIsDragging] = useState(false)

  if (!categories || categories.length === 0) {
    return null
  }

  const getCategoryIcon = (category: Category) => {
    return categoryIcons[category.slug || ""] || categoryIcons[category.id] || Box
  }

  const handleDragEnd = (_event: MouseEvent | TouchEvent | PointerEvent, info: PanInfo) => {
    const { offset, velocity } = info
    const swipe = Math.abs(offset.x) * velocity.x

    if (offset.x < -SWIPE_THRESHOLD || swipe < -1000) {
      setActiveIndex((prev) => (prev + 1) % categories.length)
      haptic.selection()
    } else if (offset.x > SWIPE_THRESHOLD || swipe > 1000) {
      setActiveIndex((prev) => (prev - 1 + categories.length) % categories.length)
      haptic.selection()
    }
    setIsDragging(false)
  }

  const handleCardClick = (category: Category) => {
    if (isDragging) return
    haptic.light()

    if (onCategorySelect) {
      // Toggle: if already selected, deselect (show all)
      if (selectedCategoryId === category.id) {
        onCategorySelect(null)
      } else {
        onCategorySelect(category.id)
      }
    }
  }

  const handleLayoutChange = (mode: LayoutMode) => {
    haptic.selection()
    setLayout(mode)
  }

  const getStackOrder = () => {
    const reordered = []
    for (let i = 0; i < categories.length; i++) {
      const index = (activeIndex + i) % categories.length
      reordered.push({ ...categories[index], stackPosition: i })
    }
    return reordered.reverse()
  }

  const getLayoutStyles = (stackPosition: number) => {
    switch (layout) {
      case "stack":
        return {
          top: stackPosition * 8,
          left: stackPosition * 8,
          zIndex: categories.length - stackPosition,
          rotate: (stackPosition - 1) * 2,
        }
      case "grid":
      case "list":
        return {
          top: 0,
          left: 0,
          zIndex: 1,
          rotate: 0,
        }
    }
  }

  const containerStyles = {
    stack: "relative h-48 w-full max-w-[240px]",
    grid: "grid grid-cols-2 md:grid-cols-4 gap-2",
    list: "flex flex-col gap-2",
  }

  const displayCards = layout === "stack"
    ? getStackOrder()
    : categories.map((c, i) => ({ ...c, stackPosition: i }))

  return (
    <div className={cn("space-y-3", className)}>
      {/* Layout Toggle */}
      <div className="flex items-center justify-center gap-1 rounded-lg bg-[var(--bg-tertiary)] p-1 w-fit mx-auto">
        {(Object.keys(layoutIcons) as LayoutMode[]).map((mode) => {
          const Icon = layoutIcons[mode]
          return (
            <button
              key={mode}
              onClick={() => handleLayoutChange(mode)}
              className={cn(
                "rounded-md p-2 transition-all",
                layout === mode
                  ? "bg-[var(--primary)] text-white"
                  : "text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-secondary)]"
              )}
              aria-label={`Switch to ${mode} layout`}
            >
              <Icon className="h-4 w-4" />
            </button>
          )
        })}
      </div>

      {/* Cards Container */}
      <LayoutGroup>
        <motion.div layout className={cn(containerStyles[layout], "mx-auto")}>
          <AnimatePresence mode="popLayout">
            {displayCards.map((card) => {
              const category = card as Category & { stackPosition: number }
              const styles = getLayoutStyles(category.stackPosition)
              const isTopCard = layout === "stack" && category.stackPosition === 0
              const isSelected = selectedCategoryId === category.id
              const IconComponent = getCategoryIcon(category)

              return (
                <motion.div
                  key={category.id}
                  layoutId={category.id}
                  initial={{ opacity: 0, scale: 0.8 }}
                  animate={{
                    opacity: 1,
                    scale: 1,
                    x: 0,
                    ...styles,
                  }}
                  exit={{ opacity: 0, scale: 0.8, x: -200 }}
                  transition={{
                    type: "spring",
                    stiffness: 300,
                    damping: 25,
                  }}
                  drag={isTopCard ? "x" : false}
                  dragConstraints={{ left: 0, right: 0 }}
                  dragElastic={0.7}
                  onDragStart={() => setIsDragging(true)}
                  onDragEnd={handleDragEnd}
                  whileDrag={{ scale: 1.02, cursor: "grabbing" }}
                  whileTap={{ scale: 0.98 }}
                  onClick={() => handleCardClick(category)}
                  className={cn(
                    "cursor-pointer rounded-[var(--radius-md)] bg-[var(--bg-surface)] p-4",
                    "border-2 transition-all",
                    isSelected
                      ? "border-[var(--primary)] bg-[var(--primary)]/5"
                      : "border-[var(--shadow-dark)]/30 hover:border-[var(--primary)]/50",
                    "shadow-sm hover:shadow-md",
                    layout === "stack" && "absolute w-[220px] h-[140px]",
                    layout === "stack" && isTopCard && "cursor-grab active:cursor-grabbing",
                    layout === "grid" && "w-full min-h-[70px]",
                    layout === "list" && "w-full"
                  )}
                >
                  <div className={cn(
                    "flex items-center h-full",
                    layout === "grid" && "flex-col justify-center gap-2",
                    layout === "list" && "gap-3",
                    layout === "stack" && "gap-3"
                  )}>
                    <div className={cn(
                      "flex shrink-0 items-center justify-center rounded-lg",
                      isSelected ? "bg-[var(--primary)]/20" : "bg-[var(--primary)]/10",
                      layout === "grid" && "h-8 w-8",
                      layout === "list" && "h-10 w-10",
                      layout === "stack" && "h-12 w-12"
                    )}>
                      <IconComponent className={cn(
                        "text-[var(--primary)]",
                        layout === "grid" && "h-4 w-4",
                        layout === "list" && "h-5 w-5",
                        layout === "stack" && "h-6 w-6"
                      )} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <h3 className={cn(
                        "font-medium",
                        isSelected ? "text-[var(--primary)]" : "text-[var(--text-primary)]",
                        layout === "grid" && "text-xs text-center leading-tight",
                        layout === "list" && "text-sm",
                        layout === "stack" && "text-base"
                      )}>
                        {category.name}
                      </h3>
                    </div>
                  </div>

                  {isTopCard && categories.length > 1 && (
                    <div className="absolute bottom-2 left-0 right-0 text-center">
                      <span className="text-[10px] text-[var(--text-muted)]">Свайп для навигации</span>
                    </div>
                  )}
                </motion.div>
              )
            })}
          </AnimatePresence>
        </motion.div>
      </LayoutGroup>

      {/* Dots indicator for stack mode */}
      {layout === "stack" && categories.length > 1 && (
        <div className="flex justify-center gap-1.5">
          {categories.map((_, index) => (
            <button
              key={index}
              onClick={() => {
                haptic.light()
                setActiveIndex(index)
              }}
              className={cn(
                "h-1.5 rounded-full transition-all",
                index === activeIndex
                  ? "w-4 bg-[var(--primary)]"
                  : "w-1.5 bg-[var(--text-muted)]/30 hover:bg-[var(--text-muted)]/50"
              )}
              aria-label={`Go to category ${index + 1}`}
            />
          ))}
        </div>
      )}
    </div>
  )
}
