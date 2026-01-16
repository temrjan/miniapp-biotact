import { useEffect, useState, useCallback } from 'react';
import { api, Category, CategoryCreate } from '../services/api';
import { Modal, Pagination } from '../components/common';

const PAGE_SIZE = 20;
const emptyCategory: CategoryCreate = { name: '', name_uz: null, slug: '', description: null, description_uz: null, image_url: null, sort_order: 0, is_active: true };

export function Categories() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [reordering, setReordering] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [formData, setFormData] = useState<CategoryCreate>(emptyCategory);
  const [currentPage, setCurrentPage] = useState(1);

  const fetchCategories = useCallback(async () => {
    try {
      const data = await api.getCategories();
      setCategories(data.items.sort((a: Category, b: Category) => a.sort_order - b.sort_order));
    } catch { setError('Error loading'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchCategories(); }, [fetchCategories]);

  const totalPages = Math.ceil(categories.length / PAGE_SIZE);
  const paginatedCategories = categories.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  const moveCategory = async (id: number, dir: 'up' | 'down') => {
    const idx = categories.findIndex(c => c.id === id);
    const newIdx = dir === 'up' ? idx - 1 : idx + 1;
    if (newIdx < 0 || newIdx >= categories.length) return;
    setReordering(true);
    try {
      const arr = [...categories];
      [arr[idx], arr[newIdx]] = [arr[newIdx], arr[idx]];
      await Promise.all([api.updateCategory(arr[idx].id, { sort_order: idx + 1 }), api.updateCategory(arr[newIdx].id, { sort_order: newIdx + 1 })]);
      arr[idx].sort_order = idx + 1; arr[newIdx].sort_order = newIdx + 1;
      setCategories(arr); setSuccess('Saved'); setTimeout(() => setSuccess(null), 2000);
    } catch { setError('Error'); } finally { setReordering(false); }
  };

  const openEdit = (c: Category) => { setFormData({ name: c.name, name_uz: c.name_uz, slug: c.slug, description: c.description, description_uz: c.description_uz, image_url: c.image_url, sort_order: c.sort_order, is_active: c.is_active }); setEditingId(c.id); setShowModal(true); };
  const openCreate = () => { setFormData({ ...emptyCategory, sort_order: categories.length + 1 }); setEditingId(null); setShowModal(true); };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      editingId ? await api.updateCategory(editingId, formData) : await api.createCategory(formData);
      setShowModal(false); fetchCategories(); setSuccess('OK'); setTimeout(() => setSuccess(null), 2000);
    } catch { setError('Error'); }
  };

  const handleDelete = async (id: number) => { if (!confirm('Delete?')) return; try { await api.deleteCategory(id); fetchCategories(); } catch { setError('Error'); } };

  if (loading) return <div>Loading...</div>;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 20 }}>
        <h1>Categories ({categories.length})</h1>
        <button className='btn btn-primary' onClick={openCreate}>+ Add</button>
      </div>
      {error && <div className='alert alert-error'>{error}</div>}
      {success && <div className='alert alert-success'>{success}</div>}
      <div className='card'>
        <table>
          <thead><tr><th>Order</th><th>ID</th><th>Name</th><th>Slug</th><th>Active</th><th>Actions</th></tr></thead>
          <tbody>{paginatedCategories.map((c, i) => (
            <tr key={c.id}>
              <td><button disabled={i === 0 || reordering} onClick={() => moveCategory(c.id, 'up')}>Up</button><button disabled={i === paginatedCategories.length - 1 || reordering} onClick={() => moveCategory(c.id, 'down')}>Dn</button></td>
              <td>{c.id}</td><td>{c.name}</td><td>{c.slug}</td><td>{c.is_active ? 'Y' : 'N'}</td>
              <td><button className='btn btn-sm btn-primary' onClick={() => openEdit(c)}>Edit</button><button className='btn btn-sm btn-danger' onClick={() => handleDelete(c.id)}>Del</button></td>
            </tr>
          ))}</tbody>
        </table>
        <Pagination currentPage={currentPage} totalPages={totalPages} onPageChange={setCurrentPage} />
      </div>
      <Modal isOpen={showModal} onClose={() => setShowModal(false)} title={editingId ? 'Edit' : 'New'}>
        <form onSubmit={handleSubmit}>
          <div className='form-group'><label>Name *</label><input value={formData.name} onChange={e => setFormData({ ...formData, name: e.target.value })} required /></div>
          <div className='form-group'><label>Name UZ</label><input value={formData.name_uz || ''} onChange={e => setFormData({ ...formData, name_uz: e.target.value || null })} /></div>
          <div className='form-group'><label>Slug *</label><input value={formData.slug} onChange={e => setFormData({ ...formData, slug: e.target.value })} required /></div>
          <div className='form-group'><label>Sort</label><input type='number' value={formData.sort_order} onChange={e => setFormData({ ...formData, sort_order: +e.target.value })} /></div>
          <div className='form-group'><label><input type='checkbox' checked={formData.is_active} onChange={e => setFormData({ ...formData, is_active: e.target.checked })} /> Active</label></div>
          <div className='modal-actions'><button type='button' className='btn' onClick={() => setShowModal(false)}>Cancel</button><button type='submit' className='btn btn-primary'>Save</button></div>
        </form>
      </Modal>
    </div>
  );
}
export default Categories;
