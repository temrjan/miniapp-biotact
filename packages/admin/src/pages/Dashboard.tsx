import { useEffect, useState } from "react";
import { api } from "../services/api";

export default function Dashboard() {
  const [stats, setStats] = useState({
    categories: 0,
    products: 0,
    activeProducts: 0,
  });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function fetchStats() {
      try {
        const [cats, prods] = await Promise.all([
          api.getCategories(),
          api.getProducts(),
        ]);

        setStats({
          categories: cats.total,
          products: prods.total,
          activeProducts: prods.items.filter((p) => p.is_active).length,
        });
      } catch (err) {
        console.error("Failed to fetch stats:", err);
      } finally {
        setLoading(false);
      }
    }

    fetchStats();
  }, []);

  if (loading) {
    return <div className="loading">Загрузка...</div>;
  }

  return (
    <div>
      <h1 style={{ marginBottom: 30 }}>📊 Дашборд</h1>

      <div className="stats-grid">
        <div className="stat-card">
          <h3>{stats.categories}</h3>
          <p>Категорий</p>
        </div>

        <div className="stat-card">
          <h3>{stats.products}</h3>
          <p>Всего товаров</p>
        </div>

        <div className="stat-card">
          <h3>{stats.activeProducts}</h3>
          <p>Активных товаров</p>
        </div>
      </div>

      <div className="card">
        <h2>🚀 Быстрые действия</h2>
        <div style={{ display: "flex", gap: 10, marginTop: 15 }}>
          <a href="/admin/categories" className="btn btn-primary">
            ➕ Добавить категорию
          </a>
          <a href="/admin/products" className="btn btn-primary">
            ➕ Добавить товар
          </a>
        </div>
      </div>
    </div>
  );
}
