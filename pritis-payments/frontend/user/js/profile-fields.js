// Phone, university and level fields, shared by the signup and complete-profile pages.
import { api } from './api.js';

/**
 * Fills the university <datalist> (searchable as you type, by name or short name like
 * "UNILAG") and returns a function that turns what the user typed into a university id.
 */
export async function setupUniversityPicker(datalist) {
  const universities = await api.get('/universities');
  const byKey = new Map();
  const options = universities.map((u) => {
    const option = document.createElement('option');
    option.value = u.name;
    if (u.short_name) option.label = u.short_name;
    byKey.set(u.name.toLowerCase(), u.id);
    if (u.short_name) byKey.set(u.short_name.toLowerCase(), u.id);
    return option;
  });
  datalist.replaceChildren(...options);
  return (typed) => byKey.get(String(typed).trim().toLowerCase()) ?? null;
}

const NG_MOBILE = /^(?:\+?234|0)?[789][01]\d{8}$/;

/** Reads and checks the three fields. Returns {values} or {error}. */
export function readProfileFields(form, findUniversity) {
  const phone = String(form.elements.phone.value).trim();
  const universityId = findUniversity(form.elements.university.value);
  const level = form.elements.level.value;

  if (!NG_MOBILE.test(phone.replace(/[\s\-().]/g, ''))) {
    return { error: 'Enter a valid Nigerian mobile number, for example 0803 123 4567.' };
  }
  if (!universityId) return { error: 'Choose your university from the list.' };
  if (!level) return { error: 'Choose your level.' };
  return { values: { phone, university_id: universityId, level } };
}
