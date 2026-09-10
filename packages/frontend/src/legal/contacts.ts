export interface LegalContacts {
  supportBot: string;
  email: string;
  phones: string[];
  postalAddress: string;
}

export const LEGAL_CONTACTS: LegalContacts = {
  supportBot: '@BiotactBot',
  email: 'info_uz@biotact.de',
  phones: ['+998 93 505-34-84', '+998 93 500-00-65'],
  postalAddress: '100109, Республика Узбекистан, г. Ташкент, Алмазарский район, ул. Фаробий, 448J',
};
