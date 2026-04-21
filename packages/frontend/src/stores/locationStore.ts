import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export interface DeliveryAddress {
  street: string;
  house: string;
  apartment: string;
  entrance: string;
  floor: string;
  comment: string;
  lat?: number | undefined;
  lng?: number | undefined;
}

interface LocationState {
  address: DeliveryAddress | null;
  isModalOpen: boolean;
  setAddress: (address: DeliveryAddress) => void;
  clearAddress: () => void;
  openModal: () => void;
  closeModal: () => void;
}

export const useLocationStore = create<LocationState>()(
  persist(
    (set) => ({
      address: null,
      isModalOpen: false,
      setAddress: (address) => set({ address, isModalOpen: false }),
      clearAddress: () => set({ address: null }),
      openModal: () => set({ isModalOpen: true }),
      closeModal: () => set({ isModalOpen: false }),
    }),
    {
      name: 'biotact-location',
    }
  )
);

export function getDisplayAddress(address: DeliveryAddress | null): string {
  if (!address || !address.street) {
    return 'Укажите адрес доставки';
  }
  
  let display = address.street;
  if (address.house) {
    display += ', д. ' + address.house;
  }
  if (address.apartment) {
    display += ', кв. ' + address.apartment;
  }
  
  return display;
}
