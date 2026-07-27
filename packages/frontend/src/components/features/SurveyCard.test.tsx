import type { ComponentProps } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { loyaltyApi } from '@/api';
import { SurveyCard } from './SurveyCard';

vi.mock('@/api', () => ({ loyaltyApi: { survey: vi.fn() } }));

const surveyMock = vi.mocked(loyaltyApi.survey);

type SurveyCardProps = ComponentProps<typeof SurveyCard>;

// Default = a member who never answered (all nulls) — the pre-existing "always ask" state.
function setup(props: Partial<SurveyCardProps> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(client, 'invalidateQueries');
  const view = render(
    <QueryClientProvider client={client}>
      <SurveyCard gender={null} age={null} city={null} isAdult={false} {...props} />
    </QueryClientProvider>
  );
  // Simulates the refetched summary arriving: LoyaltySection re-renders us with new props.
  const rerenderWith = (next: Partial<SurveyCardProps>) =>
    view.rerender(
      <QueryClientProvider client={client}>
        <SurveyCard gender={null} age={null} city={null} isAdult={false} {...next} />
      </QueryClientProvider>
    );
  return { invalidateSpy, rerenderWith };
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

  it('collapses the form after the first save once the refetched summary flips the props', async () => {
    surveyMock.mockResolvedValue({ updatedFields: ['city'], surveyDone: true });
    const { invalidateSpy, rerenderWith } = setup();

    fireEvent.change(screen.getByLabelText('Город'), { target: { value: 'Ташкент' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['loyalty', 'me'] })
    );

    // The invalidated query refetches; LoyaltySection re-renders us with the saved survey.
    rerenderWith({ city: 'Ташкент' });

    expect(screen.getByText('Анкета заполнена')).toBeInTheDocument();
    expect(screen.queryByLabelText('Город')).not.toBeInTheDocument();
  });

  it('collapses to the filled state instead of the form once the survey is saved', () => {
    setup({ gender: 'male', age: 47, city: 'Ташкент' });

    expect(screen.getByText('Анкета заполнена')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Изменить' })).toBeInTheDocument();
    expect(screen.queryByLabelText('Город')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Сохранить' })).not.toBeInTheDocument();
  });

  it('treats a partially answered survey (single field) as filled', () => {
    setup({ city: 'Ташкент' });

    expect(screen.getByText('Анкета заполнена')).toBeInTheDocument();
    expect(screen.queryByLabelText('Город')).not.toBeInTheDocument();
  });

  it('opens the form pre-filled with the saved values on edit', () => {
    setup({ gender: 'male', age: 47, city: 'Ташкент' });

    fireEvent.click(screen.getByRole('button', { name: 'Изменить' }));

    expect(screen.getByLabelText('Пол')).toHaveValue('male');
    expect(screen.getByLabelText('Возраст')).toHaveValue(47);
    expect(screen.getByLabelText('Город')).toHaveValue('Ташкент');
  });

  it('cancel in edit mode returns to the filled state without saving', () => {
    setup({ gender: 'male', age: 47, city: 'Ташкент' });

    fireEvent.click(screen.getByRole('button', { name: 'Изменить' }));
    fireEvent.change(screen.getByLabelText('Город'), { target: { value: 'Самарканд' } });
    fireEvent.click(screen.getByRole('button', { name: 'Отмена' }));

    expect(screen.getByText('Анкета заполнена')).toBeInTheDocument();
    expect(screen.queryByLabelText('Город')).not.toBeInTheDocument();
    expect(surveyMock).not.toHaveBeenCalled();
  });

  // 18+ self-confirmation (Stage 5, PR-5.2). It is a legal condition of lottery entry, not a
  // demographic answer: it can be given alone, and giving it alone must NOT collapse the card
  // into "survey filled" (the server likewise does not count it as a completed survey).
  it('enables the save button when only the 18+ box is ticked', () => {
    setup();
    const button = screen.getByRole('button', { name: 'Сохранить' });
    expect(button).toBeDisabled();

    fireEvent.click(screen.getByLabelText('Мне есть 18 лет'));
    expect(button).not.toBeDisabled();
  });

  it('submits only the 18+ flag when no other field was filled', async () => {
    surveyMock.mockResolvedValue({ updatedFields: ['is_adult'], surveyDone: false });
    setup();

    fireEvent.click(screen.getByLabelText('Мне есть 18 лет'));
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(surveyMock).toHaveBeenCalledWith({ isAdult: true }));
  });

  it('renders the 18+ box ticked when the member already confirmed it', () => {
    setup({ isAdult: true });

    expect(screen.getByLabelText('Мне есть 18 лет')).toBeChecked();
  });

  it('keeps asking the survey when 18+ was confirmed but no demographic field was answered', () => {
    setup({ isAdult: true });

    expect(screen.queryByText('Анкета заполнена')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Город')).toBeInTheDocument();
  });

  it('disables save again when the stored 18+ confirmation is toggled back', () => {
    setup({ isAdult: true });
    const box = screen.getByLabelText('Мне есть 18 лет');
    const button = screen.getByRole('button', { name: 'Сохранить' });
    expect(button).toBeDisabled(); // nothing changed yet — an unchanged box is not a payload

    fireEvent.click(box); // un-tick: a real change
    expect(button).not.toBeDisabled();

    fireEvent.click(box); // back to the stored value → nothing to send
    expect(button).toBeDisabled();
  });

  it('submits the withdrawal when a stored 18+ confirmation is un-ticked', async () => {
    surveyMock.mockResolvedValue({ updatedFields: ['is_adult'], surveyDone: false });
    setup({ isAdult: true });

    fireEvent.click(screen.getByLabelText('Мне есть 18 лет'));
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    // `false` must reach the server, not be dropped as "unset" — the member corrected themselves.
    await waitFor(() => expect(surveyMock).toHaveBeenCalledWith({ isAdult: false }));
  });

  it('opens the edit form with the stored 18+ confirmation pre-ticked', () => {
    setup({ gender: 'male', age: 47, city: 'Ташкент', isAdult: true });

    fireEvent.click(screen.getByRole('button', { name: 'Изменить' }));

    expect(screen.getByLabelText('Мне есть 18 лет')).toBeChecked();
  });

  it('returns to the filled state after a successful save in edit mode', async () => {
    surveyMock.mockResolvedValue({ updatedFields: ['age'], surveyDone: false });
    setup({ gender: 'male', age: 47, city: 'Ташкент' });

    fireEvent.click(screen.getByRole('button', { name: 'Изменить' }));
    fireEvent.change(screen.getByLabelText('Возраст'), { target: { value: '30' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    // The edited form submits ALL pre-filled fields (present fields overwrite server-side).
    await waitFor(() =>
      expect(surveyMock).toHaveBeenCalledWith({ gender: 'male', age: 30, city: 'Ташкент' })
    );
    expect(await screen.findByText('Анкета заполнена')).toBeInTheDocument();
    expect(screen.queryByLabelText('Возраст')).not.toBeInTheDocument();
  });
});
