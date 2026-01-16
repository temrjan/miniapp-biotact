import { useState, useEffect } from 'react';
import { useSearchParams, Link } from 'react-router-dom';

export function VerifyEmailPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token');

  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    if (!token) {
      setError('Invalid verification link');
      setIsLoading(false);
      return;
    }

    const verifyEmail = async () => {
      try {
        const response = await fetch(`/api/v1/auth/verify-email?token=${token}`);
        const data = await response.json();

        if (!response.ok) {
          throw new Error(data.detail || 'Verification failed');
        }

        setSuccess(true);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'An error occurred');
      } finally {
        setIsLoading(false);
      }
    };

    verifyEmail();
  }, [token]);

  return (
    <div className="login-page">
      <div className="login-container">
        {isLoading ? (
          <div className="login-header">
            <h1>Проверка...</h1>
            <p>Подтверждаем ваш email...</p>
          </div>
        ) : success ? (
          <>
            <div className="login-header">
              <h1>Email подтверждён!</h1>
              <p>Ваш email успешно подтверждён. Теперь вы можете войти.</p>
            </div>
            <div className="login-footer">
              <Link to="/login" className="login-btn">
                Войти
              </Link>
            </div>
          </>
        ) : (
          <>
            <div className="login-header">
              <h1>Ошибка</h1>
              <p>{error}</p>
            </div>
            <div className="login-footer">
              <Link to="/login" className="login-btn">
                Вернуться к входу
              </Link>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
