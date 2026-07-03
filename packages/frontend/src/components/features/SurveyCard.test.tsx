import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { loyaltyApi } from '@/api';
import { SurveyCard } from './SurveyCard';

vi.mock('@/api', () => ({ loyaltyApi: { survey: vi.fn() } }));

const surveyMock = vi.mocked(loyaltyApi.survey);

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(client, 'invalidateQueries');
  render(
    <QueryClientProvider client={client}>
      <SurveyCard />
    </QueryClientProvider>
  );
  return { invalidateSpy };
}

describe('SurveyCard', () => {
  beforeEach(() => {
    surveyMock.mockReset();
  });

  it('disables the save button until at least one field is filled', () => {
    setup();
    const button = screen.getByRole('button', { name: 'Сохранить' });
    expect(button).toBeDisabled();

    fireEvent.change(screen.getByLabelText('Город'), { target: { value: 'Tashkent' } });
    expect(button).not.toBeDisabled();
  });

  it('re-disables the save button when the only filled field is cleared', () => {
    setup();
    const cityInput = screen.getByLabelText('Город');
    const button = screen.getByRole('button', { name: 'Сохранить' });

    fireEvent.change(cityInput, { target: { value: 'Tashkent' } });
    expect(button).not.toBeDisabled();

    fireEvent.change(cityInput, { target: { value: '' } });
    expect(button).toBeDisabled();
  });

  it('submits only the filled fields and invalidates the loyalty summary', async () => {
    surveyMock.mockResolvedValue({ updatedFields: ['age'], surveyDone: true });
    const { invalidateSpy } = setup();

    fireEvent.change(screen.getByLabelText('Возраст'), { target: { value: '30' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(surveyMock).toHaveBeenCalledWith({ age: 30 }));
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['loyalty', 'me'] })
    );
  });

  it('shows a confirmation message on success without hiding the form', async () => {
    surveyMock.mockResolvedValue({ updatedFields: ['city'], surveyDone: true });
    setup();

    fireEvent.change(screen.getByLabelText('Город'), { target: { value: 'Samarkand' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(await screen.findByText('Спасибо! Анкета сохранена.')).toBeInTheDocument();
    expect(screen.getByLabelText('Город')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Сохранить' })).not.toBeDisabled();
  });

  it('shows an error and keeps the form on a failed save', async () => {
    surveyMock.mockRejectedValue(new Error('boom'));
    setup();

    fireEvent.change(screen.getByLabelText('Возраст'), { target: { value: '25' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(await screen.findByText(/Что-то пошло не так/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeInTheDocument();
  });
});
