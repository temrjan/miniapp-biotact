import { useEffect, useState } from 'react';
import { api, Product, ProductCreate, Category } from '../services/api';
import { Modal, Table, Pagination, MultipleImageUpload } from '../components/common';

interface ProductFormData extends Omit<ProductCreate, 'images' | 'advantages'> {
  images: string[] | null;
  advantages: string | null; // Store as newline-separated string for textarea
}

const emptyProduct: ProductFormData = {
  name: '',
  name_uz: null,
  slug: '',
  description: null,
  description_uz: null,
  price: 0,
  old_price: null,
  image_url: null,
  images: null,
  category_id: 0,
  stock_quantity: 0,
  is_active: true,
  mxik: null,
  package_code: null,
  vat: null,
  advantages: null,
  video_url: null,
  additional_info_title: null,
  additional_info_title_uz: null,
  additional_info: null,
  additional_info_uz: null,
  disclaimer: null,
  disclaimer_uz: null,
};

const PAGE_SIZE = 20;

export function Products() {
  const [products, setProducts] = useState<Product[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [filterCategoryId, setFilterCategoryId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [formData, setFormData] = useState<ProductFormData>(emptyProduct);
  const [uploading, setUploading] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [productImages, setProductImages] = useState<string[]>([]);

  useEffect(() => {
    fetchData(currentPage, filterCategoryId);
  }, [currentPage, filterCategoryId]);

  async function fetchData(page = 1, categoryId?: number | null) {
    try {
      setLoading(true);
      const [prodsRes, catsRes] = await Promise.all([
        api.getProducts(page, PAGE_SIZE, categoryId || undefined),
        api.getCategories(),
      ]);
      setProducts(prodsRes.items);
      setTotalPages(prodsRes.pages || Math.ceil(prodsRes.total / PAGE_SIZE));
      setCategories(catsRes.items);
    } catch (err) {
      setError('Ошибка загрузки');
    } finally {
      setLoading(false);
    }
  }


  function openCreate() {
    setFormData({ ...emptyProduct, category_id: categories[0]?.id || 0 });
    setEditingId(null);
    setProductImages([]);
    setShowModal(true);
  }

  function openEdit(product: Product) {
    const images = product.images || [];
    // Convert advantages array to newline-separated string for textarea
    const advantagesStr = product.advantages ? product.advantages.join('\n') : null;
    setFormData({
      name: product.name,
      name_uz: product.name_uz,
      slug: product.slug,
      description: product.description,
      description_uz: product.description_uz,
      price: product.price,
      old_price: product.old_price,
      image_url: product.image_url,
      images: product.images,
      category_id: product.category_id,
      stock_quantity: product.stock_quantity,
      is_active: product.is_active,
      mxik: product.mxik,
      package_code: product.package_code,
      vat: product.vat,
      advantages: advantagesStr,
      video_url: product.video_url,
      additional_info_title: product.additional_info_title,
      additional_info_title_uz: product.additional_info_title_uz,
      additional_info: product.additional_info,
      additional_info_uz: product.additional_info_uz,
      disclaimer: product.disclaimer,
      disclaimer_uz: product.disclaimer_uz,
    });
    setEditingId(product.id);
    setProductImages(images);
    setShowModal(true);
  }

  function closeModal() {
    setShowModal(false);
    setError(null);
  }

  function handleImagesChange(images: string[]) {
    setProductImages(images);
    const mainImage = images.length > 0 ? images[0] : null;
    setFormData({
      ...formData,
      image_url: mainImage,
      images: images.length > 0 ? images : null,
    });
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      // Convert advantages string to array (split by newlines)
      const advantagesArray = formData.advantages
        ? formData.advantages.split('\n').map(s => s.trim()).filter(s => s.length > 0)
        : null;
      const dataToSend = {
        ...formData,
        advantages: advantagesArray && advantagesArray.length > 0 ? advantagesArray : null,
        images: productImages.length > 0 ? productImages : null,
        image_url: productImages.length > 0 ? productImages[0] : formData.image_url,
      };
      if (editingId) {
        await api.updateProduct(editingId, dataToSend);
      } else {
        await api.createProduct(dataToSend as ProductCreate);
      }
      setSuccess(editingId ? 'Сохранено' : 'Создано');
      setTimeout(() => setSuccess(null), 1500);
      setShowModal(false);
      fetchData(currentPage, filterCategoryId);
    } catch (err) {
      setError('Ошибка сохранения');
    }
  }

  async function handleDelete(product: Product) {
    if (!confirm(`Удалить ${product.name}?`)) return;
    try {
      await api.deleteProduct(product.id);
      setSuccess('Удалено');
      setTimeout(() => setSuccess(null), 1500);
      fetchData(currentPage, filterCategoryId);
    } catch (err) {
      setError('Ошибка удаления');
    }
  }

  function handlePageChange(page: number) {
    if (page >= 1 && page <= totalPages) {
      setCurrentPage(page);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  }

  const columns = [
    { header: 'ID', accessor: 'id' as keyof Product, width: 60 },
    {
      header: 'Фото',
      render: (p: Product) => {
        const images = p.images || [];
        const imageUrl = images.length > 0 ? images[0] : p.image_url;
        const imageCount = images.length;
        return imageUrl ? (
          <div style={{ position: 'relative', width: 50, height: 50 }}>
            <img src={imageUrl} alt={p.name} style={{ width: 50, height: 50, objectFit: 'cover', borderRadius: 4 }} />
            {imageCount > 1 && (
              <span style={{ position: 'absolute', bottom: 2, right: 2, background: 'rgba(0,0,0,0.7)', color: '#fff', fontSize: 10, padding: '1px 4px', borderRadius: 3 }}>
                +{imageCount - 1}
              </span>
            )}
          </div>
        ) : (
          <div style={{ width: 50, height: 50, background: '#f0f0f0', borderRadius: 4 }} />
        );
      },
      width: 70,
    },
    { header: 'Название', accessor: 'name' as keyof Product },
    { header: 'Цена', render: (p: Product) => `${p.price} сум`, width: 120 },
    { header: 'Остаток', accessor: 'stock_quantity' as keyof Product, width: 80 },
  ];

  if (loading && products.length === 0) {
    return <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div>;
  }

  const gridStyle = { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' };
  const sectionStyle = { marginBottom: '20px', padding: '16px', background: '#f9f9f9', borderRadius: '8px' };
  const sectionTitleStyle = { margin: '0 0 12px 0', fontSize: '14px', fontWeight: 600, color: '#333', borderBottom: '1px solid #ddd', paddingBottom: '8px' };

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <h1 style={{ margin: 0 }}>Товары</h1>
        <button className="btn btn-primary" onClick={openCreate}>+ Новый товар</button>
      </div>

      {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}
      {success && <div className="alert alert-success" style={{ marginBottom: 16 }}>{success}</div>}

      <div style={{ marginBottom: 16 }}>
        <label style={{ marginRight: 8 }}>Категория:</label>
        <select value={filterCategoryId || ''} onChange={(e) => { setFilterCategoryId(e.target.value ? +e.target.value : null); setCurrentPage(1); }}>
          <option value="">Все</option>
          {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </div>

      <Table columns={columns} data={products} keyExtractor={(p) => p.id} onEdit={openEdit} onDelete={handleDelete} emptyMessage="Нет товаров" />
      <Pagination currentPage={currentPage} totalPages={totalPages} onPageChange={handlePageChange} disabled={loading} />

      <Modal isOpen={showModal} onClose={closeModal} title={editingId ? 'Редактировать товар' : 'Новый товар'} maxHeight="90vh">
        <form onSubmit={handleSubmit} style={{ maxHeight: '70vh', overflowY: 'auto', paddingRight: '8px' }}>

          {/* Основная информация */}
          <div style={sectionStyle}>
            <h3 style={sectionTitleStyle}>Основная информация</h3>
            <div style={gridStyle}>
              <div className="form-group">
                <label>Название (RU) *</label>
                <input type="text" value={formData.name} onChange={(e) => setFormData({ ...formData, name: e.target.value })} required />
              </div>
              <div className="form-group">
                <label>Название (UZ)</label>
                <input type="text" value={formData.name_uz || ''} onChange={(e) => setFormData({ ...formData, name_uz: e.target.value || null })} />
              </div>
            </div>
            <div style={gridStyle}>
              <div className="form-group">
                <label>Slug *</label>
                <input type="text" value={formData.slug} onChange={(e) => setFormData({ ...formData, slug: e.target.value })} required />
              </div>
              <div className="form-group">
                <label>Категория</label>
                <select value={formData.category_id} onChange={(e) => setFormData({ ...formData, category_id: +e.target.value })}>
                  {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </div>
            </div>
            <div className="form-group">
              <label style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <input type="checkbox" checked={formData.is_active} onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })} />
                Активен
              </label>
            </div>
          </div>

          {/* Цены и наличие */}
          <div style={sectionStyle}>
            <h3 style={sectionTitleStyle}>Цены и наличие</h3>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '12px' }}>
              <div className="form-group">
                <label>Цена (сум) *</label>
                <input type="number" value={formData.price || ''} onChange={(e) => setFormData({ ...formData, price: +e.target.value || 0 })} required min="0" />
              </div>
              <div className="form-group">
                <label>Старая цена (сум)</label>
                <input type="number" value={formData.old_price || ''} onChange={(e) => setFormData({ ...formData, old_price: e.target.value ? +e.target.value : null })} min="0" />
              </div>
              <div className="form-group">
                <label>Остаток</label>
                <input type="number" value={formData.stock_quantity || ''} onChange={(e) => setFormData({ ...formData, stock_quantity: +e.target.value || 0 })} min="0" />
              </div>
            </div>
          </div>

          {/* Медиа */}
          <div style={sectionStyle}>
            <h3 style={sectionTitleStyle}>Медиа</h3>
            <div className="form-group">
              <label>Фото товара (до 3 шт.)</label>
              <MultipleImageUpload images={productImages} onChange={handleImagesChange} maxImages={3} uploading={uploading} onUploadStart={() => setUploading(true)} onUploadEnd={() => setUploading(false)} />
            </div>
            <div className="form-group">
              <label>Видео URL</label>
              <input type="text" value={formData.video_url || ''} onChange={(e) => setFormData({ ...formData, video_url: e.target.value || null })} placeholder="https://youtube.com/..." />
            </div>
          </div>

          {/* Описание */}
          <div style={sectionStyle}>
            <h3 style={sectionTitleStyle}>Описание</h3>
            <div style={gridStyle}>
              <div className="form-group">
                <label>Описание (RU)</label>
                <textarea rows={4} value={formData.description || ''} onChange={(e) => setFormData({ ...formData, description: e.target.value || null })} />
              </div>
              <div className="form-group">
                <label>Описание (UZ)</label>
                <textarea rows={4} value={formData.description_uz || ''} onChange={(e) => setFormData({ ...formData, description_uz: e.target.value || null })} />
              </div>
            </div>
            <div className="form-group">
              <label>Преимущества</label>
              <textarea rows={3} value={formData.advantages || ''} onChange={(e) => setFormData({ ...formData, advantages: e.target.value || null })} placeholder="Перечислите преимущества товара" />
            </div>
          </div>

          {/* Дополнительная информация */}
          <div style={sectionStyle}>
            <h3 style={sectionTitleStyle}>Дополнительная информация</h3>
            <div style={gridStyle}>
              <div className="form-group">
                <label>Заголовок доп. инфо (RU)</label>
                <input type="text" value={formData.additional_info_title || ''} onChange={(e) => setFormData({ ...formData, additional_info_title: e.target.value || null })} />
              </div>
              <div className="form-group">
                <label>Заголовок доп. инфо (UZ)</label>
                <input type="text" value={formData.additional_info_title_uz || ''} onChange={(e) => setFormData({ ...formData, additional_info_title_uz: e.target.value || null })} />
              </div>
            </div>
            <div style={gridStyle}>
              <div className="form-group">
                <label>Доп. информация (RU)</label>
                <textarea rows={3} value={formData.additional_info || ''} onChange={(e) => setFormData({ ...formData, additional_info: e.target.value || null })} />
              </div>
              <div className="form-group">
                <label>Доп. информация (UZ)</label>
                <textarea rows={3} value={formData.additional_info_uz || ''} onChange={(e) => setFormData({ ...formData, additional_info_uz: e.target.value || null })} />
              </div>
            </div>
          </div>

          {/* Дисклеймер */}
          <div style={sectionStyle}>
            <h3 style={sectionTitleStyle}>Дисклеймер</h3>
            <div style={gridStyle}>
              <div className="form-group">
                <label>Дисклеймер (RU)</label>
                <textarea rows={2} value={formData.disclaimer || ''} onChange={(e) => setFormData({ ...formData, disclaimer: e.target.value || null })} />
              </div>
              <div className="form-group">
                <label>Дисклеймер (UZ)</label>
                <textarea rows={2} value={formData.disclaimer_uz || ''} onChange={(e) => setFormData({ ...formData, disclaimer_uz: e.target.value || null })} />
              </div>
            </div>
          </div>

          {/* Налоговая информация */}
          <div style={sectionStyle}>
            <h3 style={sectionTitleStyle}>Налоговая информация</h3>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '12px' }}>
              <div className="form-group">
                <label>MXIK</label>
                <input type="text" value={formData.mxik || ''} onChange={(e) => setFormData({ ...formData, mxik: e.target.value || null })} />
              </div>
              <div className="form-group">
                <label>Код упаковки</label>
                <input type="text" value={formData.package_code || ''} onChange={(e) => setFormData({ ...formData, package_code: e.target.value || null })} />
              </div>
              <div className="form-group">
                <label>НДС (%)</label>
                <input type="number" value={formData.vat || ''} onChange={(e) => setFormData({ ...formData, vat: e.target.value ? +e.target.value : null })} min="0" max="100" />
              </div>
            </div>
          </div>

          {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}
          <div className="modal-actions">
            <button type="button" className="btn" onClick={closeModal}>Отмена</button>
            <button type="submit" className="btn btn-primary" disabled={uploading}>{editingId ? 'Сохранить' : 'Создать'}</button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
