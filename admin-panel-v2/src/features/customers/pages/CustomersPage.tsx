import { useState } from "react"
import { Search } from "lucide-react"
import { Input } from "@/shared/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/shared/components/ui/select"
import { Button } from "@/shared/components/ui/button"
import { useCustomers, useCustomersStats } from "../hooks/useCustomers"
import { StatsCards } from "../components/StatsCards"
import { CustomerCard } from "../components/CustomerCard"
import { PROBLEM_LABELS, type ProblemTag } from "../types/customer.types"

export function CustomersPage() {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState("")
  const [problem, setProblem] = useState<ProblemTag | "all">("all")
  const [club, setClub] = useState<"all" | "member" | "qr">("all")

  const { data: stats, isLoading: statsLoading } = useCustomersStats()
  const { data: customers, isLoading: customersLoading } = useCustomers({
    page,
    size: 12,
    search: search || undefined,
    problem: problem === "all" ? undefined : problem,
    club: club === "all" ? undefined : club,
  })

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">CRM</h1>
        <p className="text-muted-foreground">
          Клиенты с данными от AI-ассистента
        </p>
      </div>

      {/* Stats */}
      <StatsCards stats={stats} isLoading={statsLoading} />

      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-4">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Поиск по имени или телефону..."
            value={search}
            onChange={(e) => {
              setSearch(e.target.value)
              setPage(1)
            }}
            className="pl-10"
          />
        </div>
        <Select
          value={problem}
          onValueChange={(value) => {
            setProblem(value as ProblemTag | "all")
            setPage(1)
          }}
        >
          <SelectTrigger className="w-full sm:w-[180px]">
            <SelectValue placeholder="Все проблемы" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Все проблемы</SelectItem>
            {Object.entries(PROBLEM_LABELS).map(([key, label]) => (
              <SelectItem key={key} value={key}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={club}
          onValueChange={(value) => {
            setClub(value as "all" | "member" | "qr")
            setPage(1)
          }}
        >
          <SelectTrigger className="w-full sm:w-[200px]">
            <SelectValue placeholder="Biotact Club" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Все клиенты</SelectItem>
            <SelectItem value="member">Участники Клуба</SelectItem>
            <SelectItem value="qr">Пришли по QR</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Customers Grid */}
      {customersLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <div key={i} className="h-48 bg-gray-100 rounded-lg animate-pulse" />
          ))}
        </div>
      ) : customers?.items && customers.items.length > 0 ? (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {customers.items.map((customer) => (
              <CustomerCard key={customer.telegram_id} customer={customer} />
            ))}
          </div>

          {/* Pagination */}
          {customers.pages > 1 && (
            <div className="flex justify-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
              >
                Назад
              </Button>
              <span className="flex items-center px-4 text-sm text-muted-foreground">
                {page} / {customers.pages}
              </span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => Math.min(customers.pages, p + 1))}
                disabled={page === customers.pages}
              >
                Вперёд
              </Button>
            </div>
          )}
        </>
      ) : (
        <div className="text-center py-12">
          <p className="text-muted-foreground">
            {search || problem !== "all" || club !== "all"
              ? "Клиенты не найдены"
              : "Пока нет клиентов с CRM-данными"}
          </p>
        </div>
      )}
    </div>
  )
}
