import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { Button } from "@/shared/components/ui/button"
import { Input } from "@/shared/components/ui/input"
import { Label } from "@/shared/components/ui/label"
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/shared/components/ui/card"
import { Logo } from "@/shared/components/common"
import { useLogin } from "../hooks/useAuth"

const loginSchema = z.object({
  username: z.string().min(1, "Введите имя пользователя"),
  password: z.string().min(1, "Введите пароль"),
})

type LoginFormData = z.infer<typeof loginSchema>

export const LoginPage = () => {
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<LoginFormData>({
    resolver: zodResolver(loginSchema),
  })

  const login = useLogin()

  const onSubmit = (data: LoginFormData) => {
    login.mutate(data)
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-primary/10 via-background to-primary/5 p-4">
      <Card className="w-full max-w-md shadow-large">
        <CardHeader className="space-y-3 text-center">
          <div className="mx-auto w-16 h-16 bg-primary/10 rounded-2xl flex items-center justify-center">
            <Logo variant="login" />
          </div>
          <CardTitle className="text-2xl">Biotact Admin</CardTitle>
          <CardDescription>Вход в панель управления</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="username">Имя пользователя</Label>
              <Input
                id="username"
                type="text"
                placeholder="Введите имя пользователя"
                {...register("username")}
                disabled={login.isPending}
              />
              {errors.username && (
                <p className="text-sm text-destructive">{errors.username.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="password">Пароль</Label>
              <Input
                id="password"
                type="password"
                placeholder="Введите пароль"
                {...register("password")}
                disabled={login.isPending}
              />
              {errors.password && (
                <p className="text-sm text-destructive">{errors.password.message}</p>
              )}
            </div>

            {login.isError && (
              <div className="p-3 rounded-md bg-destructive/10 text-destructive text-sm">
                Неверное имя пользователя или пароль
              </div>
            )}

            <Button type="submit" className="w-full" disabled={login.isPending}>
              {login.isPending ? "Вход..." : "Войти"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}

export default LoginPage
