import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { DemographicsBreakdown } from './DemographicsBreakdown'
import type { Demographics } from '../types'

const emptyDemographics: Demographics = {
  gender: [],
  age_buckets: [],
  top_cities: [],
  survey_coverage: 0,
}

describe('DemographicsBreakdown', () => {
  it('НИТ-4: при малом N (<10) баннер малой выборки есть, над всей секцией', () => {
    render(<DemographicsBreakdown demographics={emptyDemographics} profilesTotal={3} />)
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent(/Малая выборка \(N=3\)/)
  })

  it('НИТ-4: при большом N (>=10) баннера нет', () => {
    render(<DemographicsBreakdown demographics={emptyDemographics} profilesTotal={50} />)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('gender unknown → «Не указан» (фолбэк карты)', () => {
    const demographics: Demographics = {
      gender: [
        { gender: 'male', count: 2 },
        { gender: 'unknown', count: 1 },
      ],
      age_buckets: [{ bucket: '25-34', count: 3 }],
      top_cities: [{ city: 'Ташкент', count: 3 }],
      survey_coverage: 42.5,
    }
    render(<DemographicsBreakdown demographics={demographics} profilesTotal={50} />)
    expect(screen.getByText('Муж.')).toBeInTheDocument()
    expect(screen.getByText('Не указан')).toBeInTheDocument()
    expect(screen.getByText('Ташкент')).toBeInTheDocument()
    expect(screen.getByText('Покрытие анкеты: 42.5%')).toBeInTheDocument()
  })

  it('Ф2: неизвестные gender/bucket (ВНЕ карты) рендерятся сырыми — ветка фолбэка, не «found in map»', () => {
    const demographics: Demographics = {
      gender: [{ gender: '__new_gender__', count: 1 }],
      age_buckets: [{ bucket: '__new_bucket__', count: 1 }],
      top_cities: [],
      survey_coverage: 0,
    }
    render(<DemographicsBreakdown demographics={demographics} profilesTotal={50} />)
    expect(screen.getByText('__new_gender__')).toBeInTheDocument()
    expect(screen.getByText('__new_bucket__')).toBeInTheDocument()
  })

  it('пустая демография → «Нет данных анкеты» в каждом срезе', () => {
    render(<DemographicsBreakdown demographics={emptyDemographics} profilesTotal={50} />)
    expect(screen.getAllByText('Нет данных анкеты')).toHaveLength(3)
  })
})
