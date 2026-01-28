import { Users, UserPlus, Phone, ShoppingBag } from "lucide-react"
import { Card, CardContent } from "@/shared/components/ui/card"
import type { CustomersStats } from "../types/customer.types"

interface StatsCardsProps {
  stats: CustomersStats | undefined
  isLoading: boolean
}

export function StatsCards({ stats, isLoading }: StatsCardsProps) {
  const cards = [
    {
      title: "Всего клиентов",
      value: stats?.total_customers ?? 0,
      icon: Users,
      color: "text-blue-600",
      bgColor: "bg-blue-100",
    },
    {
      title: "Новых сегодня",
      value: stats?.new_today ?? 0,
      icon: UserPlus,
      color: "text-green-600",
      bgColor: "bg-green-100",
    },
    {
      title: "С телефоном",
      value: stats?.with_phone ?? 0,
      icon: Phone,
      color: "text-purple-600",
      bgColor: "bg-purple-100",
    },
    {
      title: "С покупками",
      value: stats?.with_purchases ?? 0,
      icon: ShoppingBag,
      color: "text-orange-600",
      bgColor: "bg-orange-100",
    },
  ]

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {[1, 2, 3, 4].map((i) => (
          <Card key={i}>
            <CardContent className="p-6">
              <div className="animate-pulse">
                <div className="h-4 bg-gray-200 rounded w-24 mb-2" />
                <div className="h-8 bg-gray-200 rounded w-16" />
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    )
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
      {cards.map((card) => (
        <Card key={card.title}>
          <CardContent className="p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted-foreground">{card.title}</p>
                <p className="text-2xl font-bold mt-1">{card.value}</p>
              </div>
              <div className={`p-3 rounded-full ${card.bgColor}`}>
                <card.icon className={`h-5 w-5 ${card.color}`} />
              </div>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
