import { useState, useEffect, useRef } from 'react';
import { api, type Banner, type BannerCreate } from '../services/api';
import { Pagination } from '../components/common';

const PAGE_SIZE = 10;

export function Banners() {
  const [banners, setBanners] = useState<Banner[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [bannerInterval, setBannerInterval] = useState('3000');
  const [savingInterval, setSavingInterval] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { loadBanners(); loadSettings(); }, []);

  const loadBanners = async () => {
    try { setBanners((await api.getBanners()).items.sort((a, b) => a.sort_order - b.sort_order)); }
    catch { setError('Error loading'); }
    finally { setLoading(false); }
  };

  const loadSettings = async () => {
    try { setBannerInterval((await api.getSetting('banner_interval')).value); } catch {}
  };

  const totalPages = Math.ceil(banners.length / PAGE_SIZE);
  const paginatedBanners = banners.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  const showMsg = (msg: string, isError = false) => {
    isError ? setError(msg) : setSuccess(msg);
    setTimeout(() => isError ? setError('') : setSuccess(''), 2000);
  };

  const handleSaveInterval = async () => {
    setSavingInterval(true);
    try { await api.updateSetting('banner_interval', bannerInterval); showMsg('Saved!'); }
    catch { showMsg('Error', true); }
    finally { setSavingInterval(false); }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await fetch('/api/v1/uploads/banners', { method: 'POST', body: formData });
      if (!res.ok) throw new Error('Upload failed');
      const { url } = await res.json();
      const newBanner: BannerCreate = { title: 'Banner ' + (banners.length + 1), title_uz: null, subtitle: null, subtitle_uz: null, image_url: url, gradient: null, link_url: '/catalog', sort_order: banners.length, is_active: true };
      await api.createBanner(newBanner);
      showMsg('Added!'); loadBanners();
    } catch (err) { showMsg(err instanceof Error ? err.message : 'Error', true); }
    finally { setUploading(false); if (fileInputRef.current) fileInputRef.current.value = ''; }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Delete?')) return;
    try { await api.deleteBanner(id); showMsg('Deleted'); loadBanners(); } catch { showMsg('Error', true); }
  };

  const handleToggle = async (b: Banner) => {
    try { await api.updateBanner(b.id, { is_active: !b.is_active }); loadBanners(); } catch { showMsg('Error', true); }
  };

  const handleLinkChange = async (b: Banner, link: string) => {
    try { await api.updateBanner(b.id, { link_url: link || null }); loadBanners(); } catch { showMsg('Error', true); }
  };

  const moveBanner = async (b: Banner, dir: 'up' | 'down') => {
    const idx = banners.findIndex(x => x.id === b.id);
    const newIdx = dir === 'up' ? idx - 1 : idx + 1;
    if (newIdx < 0 || newIdx >= banners.length) return;
    try {
      await Promise.all([api.updateBanner(b.id, { sort_order: newIdx }), api.updateBanner(banners[newIdx].id, { sort_order: idx })]);
      loadBanners();
    } catch { showMsg('Error', true); }
  };

  if (loading) return <div>Loading...</div>;

  return (
    <div>
      <h1>Banners ({banners.length})</h1>
      {error && <div className='alert alert-error'>{error}</div>}
      {success && <div className='alert alert-success'>{success}</div>}

      <div className='card' style={{ marginBottom: 20, padding: 16 }}>
        <h3>Slider Settings</h3>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <label>Interval (ms):</label>
          <input type='number' value={bannerInterval} onChange={e => setBannerInterval(e.target.value)} min='1000' max='10000' step='500' style={{ width: 100 }} />
          <button className='btn btn-secondary' onClick={handleSaveInterval} disabled={savingInterval}>{savingInterval ? '...' : 'Save'}</button>
        </div>
      </div>

      <div style={{ marginBottom: 20 }}>
        <input ref={fileInputRef} type='file' accept='image/*' onChange={handleFileUpload} style={{ display: 'none' }} />
        <button className='btn btn-primary' onClick={() => fileInputRef.current?.click()} disabled={uploading}>{uploading ? 'Uploading...' : '+ Add Banner'}</button>
      </div>

      {banners.length === 0 ? (
        <div className='card' style={{ padding: 40, textAlign: 'center' }}>No banners</div>
      ) : (
        <div className='card'>
          <div style={{ display: 'grid', gap: 16, padding: 16 }}>
            {paginatedBanners.map((b, i) => (
              <div key={b.id} style={{ display: 'flex', gap: 16, padding: 12, border: '1px solid #eee', borderRadius: 8, opacity: b.is_active ? 1 : 0.5 }}>
                <div style={{ width: 200, height: 80, borderRadius: 8, overflow: 'hidden', background: b.gradient || '#1b3554' }}>
                  {b.image_url && <img src={b.image_url} alt={b.title} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />}
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ marginBottom: 8 }}>Banner {(currentPage - 1) * PAGE_SIZE + i + 1} <span style={{ fontSize: 11, padding: '2px 6px', borderRadius: 4, background: b.is_active ? '#d1fae5' : '#fef3c7' }}>{b.is_active ? 'Active' : 'Hidden'}</span></div>
                  <input type='text' defaultValue={b.link_url || ''} onBlur={e => handleLinkChange(b, e.target.value)} placeholder='Link' style={{ width: '100%' }} />
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <div style={{ display: 'flex', gap: 4 }}>
                    <button onClick={() => moveBanner(b, 'up')} disabled={banners.indexOf(b) === 0}>Up</button>
                    <button onClick={() => moveBanner(b, 'down')} disabled={banners.indexOf(b) === banners.length - 1}>Dn</button>
                  </div>
                  <button onClick={() => handleToggle(b)}>{b.is_active ? 'Hide' : 'Show'}</button>
                  <button className='btn btn-danger' onClick={() => handleDelete(b.id)}>Del</button>
                </div>
              </div>
            ))}
          </div>
          <Pagination currentPage={currentPage} totalPages={totalPages} onPageChange={setCurrentPage} />
        </div>
      )}
    </div>
  );
}
export default Banners;