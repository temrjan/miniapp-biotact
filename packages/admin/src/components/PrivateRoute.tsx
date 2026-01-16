import { ReactNode, useEffect } from 'react';
import { Navigate } from 'react-router-dom';
import { useAuthStore } from '../stores/authStore';

interface PrivateRouteProps {
  children: ReactNode;
}

export function PrivateRoute({ children }: PrivateRouteProps) {
  const { isAuthenticated, isLoading, accessToken, setLoading, logout, refreshAccessToken } = useAuthStore();

  useEffect(() => {
    const verifyToken = async () => {
      if (!accessToken) {
        setLoading(false);
        return;
      }

      try {
        const response = await fetch('/api/v1/auth/me', {
          headers: {
            'Authorization': `Bearer ${accessToken}`,
          },
        });

        if (!response.ok) {
          // Try to refresh token
          const refreshed = await refreshAccessToken();
          if (!refreshed) {
            logout();
          }
        }
      } catch {
        logout();
      } finally {
        setLoading(false);
      }
    };

    verifyToken();
  }, [accessToken, setLoading, logout, refreshAccessToken]);

  if (isLoading) {
    return (
      <div className="loading-screen">
        <div className="loading-spinner"></div>
        <p>Загрузка...</p>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}
