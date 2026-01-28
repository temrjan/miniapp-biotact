import { User, Phone, Users, ShoppingBag } from "lucide-react"
import { Card, CardContent } from "@/shared/components/ui/card"
import { Badge } from "@/shared/components/ui/badge"
import type { Customer } from "../types/customer.types"
import { PROBLEM_LABELS, PROBLEM_COLORS, type ProblemTag } from "../types/customer.types"

interface CustomerCardProps {
  customer: Customer
}

export function CustomerCard({ customer }: CustomerCardProps) {
  const fullName = [customer.first_name, customer.last_name]
    .filter(Boolean)
    .join(" ") || customer.username || `ID: ${customer.telegram_id}`

  return (
    <Card className="hover:shadow-md transition-shadow">
      <CardContent className="p-4">
        {/* Header */}
        <div className="flex items-start justify-between mb-3">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center">
              <User className="h-5 w-5 text-primary" />
            </div>
            <div>
              <h3 className="font-medium">{fullName}</h3>
              {customer.username && (
                <p className="text-sm text-muted-foreground">@{customer.username}</p>
              )}
            </div>
          </div>
        </div>

        {/* Contact */}
        {customer.phone && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground mb-3">
            <Phone className="h-4 w-4" />
            <span>{customer.phone}</span>
          </div>
        )}

        {/* Problems */}
        {customer.problems && customer.problems.length > 0 && (
          <div className="flex flex-wrap gap-1 mb-3">
            {customer.problems.map((problem) => (
              <Badge
                key={problem}
                variant="secondary"
                className={PROBLEM_COLORS[problem as ProblemTag] || "bg-gray-100"}
              >
                {PROBLEM_LABELS[problem as ProblemTag] || problem}
              </Badge>
            ))}
          </div>
        )}

        {/* Family */}
        {customer.family && customer.family.length > 0 && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground mb-3">
            <Users className="h-4 w-4" />
            <span>Семья: {customer.family.length} чел.</span>
          </div>
        )}

        {/* Purchased Products */}
        {customer.purchased_products && customer.purchased_products.length > 0 && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <ShoppingBag className="h-4 w-4" />
            <span>{customer.purchased_products.length} покупок</span>
          </div>
        )}

        {/* AI Notes */}
        {customer.ai_notes && (
          <p className="text-sm text-muted-foreground mt-3 line-clamp-2">
            {customer.ai_notes}
          </p>
        )}
      </CardContent>
    </Card>
  )
}
