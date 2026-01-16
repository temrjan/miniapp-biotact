import { useEffect, useState } from 'react';
import { api, Settings } from '../services/api';
import { Modal } from '../components/common';

interface SettingFormData {
  key: string;
  value: string;
  description: string;
}

const emptyForm: SettingFormData = {
  key: '',
  value: '',
  description: '',
};

// Предопределённые настройки для создания
const predefinedSettings = [
  { key: 'store_name', description: 'Название магазина' },
  { key: 'store_phone', description: 'Телефон магазина' },
  { key: 'store_email', description: 'Email магазина' },
  { key: 'store_address', description: 'Адрес магазина' },
  { key: 'delivery_price', description: 'Стоимость доставки (UZS)' },
  { key: 'free_delivery_from', description: 'Бесплатная доставка от суммы (UZS)' },
  { key: 'min_order_amount', description: 'Минимальная сумма заказа (UZS)' },
  { key: 'working_hours', description: 'Время работы' },
  { key: 'telegram_channel', description: 'Телеграм канал' },
  { key: 'instagram_url', description: 'Instagram URL' },
];

export function SettingsPage() {
  const [settings, setSettings] = useState<Settings[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [formData, setFormData] = useState<SettingFormData>(emptyForm);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetchSettings();
  }, []);

  async function fetchSettings() {
    try {
      setLoading(true);
      const data = await api.getSettings();
      setSettings(data);
    } catch (err) {
      setError('Ошибка загрузки настроек');
    } finally {
      setLoading(false);
    }
  }

  function openCreate() {
    setFormData(emptyForm);
    setEditingKey(null);
    setShowModal(true);
  }

  function openEdit(setting: Settings) {
    setFormData({
      key: setting.key,
      value: setting.value,
      description: setting.description || '',
    });
    setEditingKey(setting.key);
    setShowModal(true);
  }

  function closeModal() {
    setShowModal(false);
    setError(null);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!formData.key || !formData.value) {
      setError('Ключ и значение обязательны');
      return;
    }

    try {
      setSaving(true);
      await api.updateSetting(formData.key, formData.value);
      setSuccess('Сохранено');
      setTimeout(() => setSuccess(null), 1500);
      setShowModal(false);
      fetchSettings();
    } catch (err) {
      setError('Ошибка сохранения');
    } finally {
      setSaving(false);
    }
  }

  function handleSelectPredefined(preset: { key: string; description: string }) {
    setFormData({
      ...formData,
      key: preset.key,
      description: preset.description,
    });
  }

  function formatDate(dateStr: string) {
    return new Date(dateStr).toLocaleDateString('ru-RU', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  // Группировка настроек по категориям
  function getSettingCategory(key: string): string {
    if (key.startsWith('store_')) return 'Магазин';
    if (key.startsWith('delivery_') || key.startsWith('free_delivery')) return 'Доставка';
    if (key.startsWith('min_') || key.startsWith('max_')) return 'Лимиты';
    if (key.includes('telegram') || key.includes('instagram') || key.includes('url')) return 'Соц. сети';
    return 'Общее';
  }

  // Группируем настройки
  const groupedSettings = settings.reduce((acc, setting) => {
    const category = getSettingCategory(setting.key);
    if (!acc[category]) acc[category] = [];
    acc[category].push(setting);
    return acc;
  }, {} as Record<string, Settings[]>);

  if (loading) {
    return <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div>;
  }

  const cardStyle = {
    background: '#fff',
    border: '1px solid #e0e0e0',
    borderRadius: 8,
    padding: 20,
    marginBottom: 16,
  };

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 24 }}>
        <h1 style={{ margin: 0 }}>Настройки</h1>
        <button className="btn btn-primary" onClick={openCreate}>
          + Добавить настройку
        </button>
      </div>

      {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}
      {success && <div className="alert alert-success" style={{ marginBottom: 16 }}>{success}</div>}

      {settings.length === 0 ? (
        <div style={cardStyle}>
          <div style={{ textAlign: 'center', padding: 40, color: '#666' }}>
            <p>Настройки пока не заданы</p>
            <button className="btn btn-primary" onClick={openCreate}>
              Добавить первую настройку
            </button>
          </div>
        </div>
      ) : (
        Object.entries(groupedSettings).map(([category, categorySettings]) => (
          <div key={category} style={cardStyle}>
            <h3 style={{ margin: '0 0 16px 0', color: '#333', borderBottom: '1px solid #eee', paddingBottom: 8 }}>
              {category}
            </h3>
            <table className="table" style={{ margin: 0 }}>
              <thead>
                <tr>
                  <th style={{ width: 200 }}>Ключ</th>
                  <th>Значение</th>
                  <th style={{ width: 200 }}>Описание</th>
                  <th style={{ width: 150 }}>Обновлено</th>
                  <th style={{ width: 80 }}>Действия</th>
                </tr>
              </thead>
              <tbody>
                {categorySettings.map((setting) => (
                  <tr key={setting.key}>
                    <td style={{ fontFamily: 'monospace', fontSize: 13 }}>{setting.key}</td>
                    <td>
                      <div style={{
                        maxWidth: 300,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}>
                        {setting.value}
                      </div>
                    </td>
                    <td style={{ color: '#666', fontSize: 13 }}>{setting.description || '-'}</td>
                    <td style={{ color: '#666', fontSize: 12 }}>{formatDate(setting.updated_at)}</td>
                    <td>
                      <button
                        className="btn btn-small"
                        onClick={() => openEdit(setting)}
                        title="Редактировать"
                      >
                        ✏️
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))
      )}

      <Modal isOpen={showModal} onClose={closeModal} title={editingKey ? 'Редактировать настройку' : 'Новая настройка'}>
        <form onSubmit={handleSubmit}>
          {!editingKey && (
            <div className="form-group">
              <label>Выбрать из шаблонов</label>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
                {predefinedSettings
                  .filter(p => !settings.find(s => s.key === p.key))
                  .map(preset => (
                    <button
                      key={preset.key}
                      type="button"
                      className="btn btn-small"
                      onClick={() => handleSelectPredefined(preset)}
                      style={{
                        background: formData.key === preset.key ? '#4CAF50' : '#f0f0f0',
                        color: formData.key === preset.key ? '#fff' : '#333',
                      }}
                    >
                      {preset.key}
                    </button>
                  ))}
              </div>
            </div>
          )}

          <div className="form-group">
            <label>Ключ *</label>
            <input
              type="text"
              value={formData.key}
              onChange={(e) => setFormData({ ...formData, key: e.target.value })}
              disabled={!!editingKey}
              placeholder="например: store_phone"
              required
            />
          </div>

          <div className="form-group">
            <label>Значение *</label>
            <textarea
              rows={3}
              value={formData.value}
              onChange={(e) => setFormData({ ...formData, value: e.target.value })}
              placeholder="Введите значение настройки"
              required
            />
          </div>

          <div className="form-group">
            <label>Описание</label>
            <input
              type="text"
              value={formData.description}
              onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              placeholder="Краткое описание настройки"
            />
            <small style={{ color: '#666' }}>Описание сохраняется только для новых настроек</small>
          </div>

          {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}

          <div className="modal-actions">
            <button type="button" className="btn" onClick={closeModal}>Отмена</button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? 'Сохранение...' : 'Сохранить'}
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
