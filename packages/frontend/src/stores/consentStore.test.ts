import { describe, it, expect, beforeEach } from 'vitest';

import { useConsentStore } from './consentStore';

// The store exists so the consent checkboxes survive navigation to a legal document and back
// (Finding 2). In-memory only — a page reload must start from unchecked.
describe('consentStore', () => {
  beforeEach(() => {
    useConsentStore.getState().reset();
  });

  it('starts with both boxes unchecked', () => {
    const { dataProcessing, marketing } = useConsentStore.getState();
    expect(dataProcessing).toBe(false);
    expect(marketing).toBe(false);
  });

  it('sets each box independently', () => {
    useConsentStore.getState().setDataProcessing(true);
    expect(useConsentStore.getState().dataProcessing).toBe(true);
    expect(useConsentStore.getState().marketing).toBe(false);

    useConsentStore.getState().setMarketing(true);
    expect(useConsentStore.getState().marketing).toBe(true);
    expect(useConsentStore.getState().dataProcessing).toBe(true);
  });

  it('unticks a box without touching the other', () => {
    useConsentStore.getState().setDataProcessing(true);
    useConsentStore.getState().setMarketing(true);
    useConsentStore.getState().setDataProcessing(false);

    expect(useConsentStore.getState().dataProcessing).toBe(false);
    expect(useConsentStore.getState().marketing).toBe(true);
  });

  it('reset returns both boxes to unchecked', () => {
    useConsentStore.getState().setDataProcessing(true);
    useConsentStore.getState().setMarketing(true);
    useConsentStore.getState().reset();

    const { dataProcessing, marketing } = useConsentStore.getState();
    expect(dataProcessing).toBe(false);
    expect(marketing).toBe(false);
  });
});
