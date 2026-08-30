const fs = require('fs');

const path = 'c:/Users/Admin/Desktop/desktop/DMND project/Rural Health care/backend/disease_knowledge_base.json';
const kb = JSON.parse(fs.readFileSync(path, 'utf8'));

function pickPrimarySymptoms(symptomsText) {
  const parts = String(symptomsText || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  if (parts.length >= 2) return `${parts[0]}, ${parts[1]}`;
  if (parts.length === 1) return parts[0];
  return 'Mild tiredness and discomfort';
}

function derivePrevention(disease, causes, advice) {
  const d = String(disease).toLowerCase();
  const c = String(causes || '').toLowerCase();

  if (d.includes('dengue') || d.includes('malaria')) {
    return 'Avoid mosquito bites, use bed nets, and remove stagnant water.';
  }
  if (d.includes('diarrhea') || d.includes('food poisoning') || d.includes('typhoid')) {
    return 'Drink clean boiled water, maintain hand hygiene, and eat freshly cooked food.';
  }
  if (d.includes('diabetes')) {
    return 'Follow a low-sugar balanced diet, stay active, and monitor blood sugar regularly.';
  }
  if (d.includes('hypertension') || d.includes('heart')) {
    return 'Reduce salt, avoid smoking, manage stress, and do regular checkups.';
  }
  if (
    d.includes('asthma') ||
    d.includes('bronchitis') ||
    d.includes('pneumonia') ||
    d.includes('influenza') ||
    d.includes('cold')
  ) {
    return 'Maintain hygiene, avoid smoke and close contact with sick people, and take recommended vaccines.';
  }
  if (d.includes('skin') || d.includes('eczema') || d.includes('psoriasis') || d.includes('allergy')) {
    return 'Keep skin clean, avoid known triggers, and use protective care as advised.';
  }
  if (d.includes('stroke')) {
    return 'Control blood pressure, diabetes, and cholesterol, and avoid smoking and alcohol.';
  }
  if (d.includes('depression') || d.includes('anxiety')) {
    return 'Manage stress early, maintain sleep routine, and seek timely emotional support.';
  }
  if (c.includes('viral') || c.includes('virus')) {
    return 'Maintain hand hygiene, avoid close contact with infected people, and strengthen immunity with healthy habits.';
  }
  if (c.includes('bacterial') || c.includes('bacteria')) {
    return 'Maintain personal hygiene, use clean water/food, and seek early treatment for infection signs.';
  }
  if (advice) {
    return 'Follow medical guidance early, maintain hygiene, and reduce known risk factors.';
  }
  return 'Maintain healthy lifestyle habits, hygiene, and regular medical checkups.';
}

function deriveFood(disease) {
  const d = String(disease).toLowerCase();

  if (d.includes('diabetes')) {
    return 'High-fiber vegetables, whole grains, lean protein, and low-sugar meals.';
  }
  if (d.includes('hypertension') || d.includes('heart') || d.includes('stroke')) {
    return 'Low-salt, low-fat meals, fruits, vegetables, and whole grains.';
  }
  if (d.includes('diarrhea') || d.includes('food poisoning') || d.includes('dehydration')) {
    return 'ORS, clean water, soft bland foods like rice/banana/toast, and light soups.';
  }
  if (d.includes('kidney')) {
    return 'Adequate water, low-salt meals, and doctor-guided stone-specific diet.';
  }
  if (d.includes('liver') || d.includes('hepatitis')) {
    return 'Light home-cooked meals, fruits, and plenty of fluids; avoid alcohol.';
  }
  if (d.includes('dengue') || d.includes('malaria') || d.includes('influenza') || d.includes('cold') || d.includes('pneumonia')) {
    return 'Warm fluids, soups, fruits, and easy-to-digest home food.';
  }
  return 'Balanced home-cooked diet with fruits, vegetables, fluids, and adequate protein.';
}

function deriveExercise(disease) {
  const d = String(disease).toLowerCase();

  if (
    d.includes('dengue') ||
    d.includes('malaria') ||
    d.includes('pneumonia') ||
    d.includes('influenza') ||
    d.includes('appendicitis') ||
    d.includes('food poisoning') ||
    d.includes('diarrhea') ||
    d.includes('dehydration') ||
    d.includes('chickenpox') ||
    d.includes('measles')
  ) {
    return 'Rest is recommended until recovery; avoid strenuous activity.';
  }
  if (d.includes('heart') || d.includes('hypertension') || d.includes('diabetes') || d.includes('obesity')) {
    return 'Light-to-moderate activity such as daily walking, as advised by a doctor.';
  }
  if (d.includes('arthritis') || d.includes('parkinson')) {
    return 'Gentle mobility and stretching or physiotherapy-guided exercises.';
  }
  if (d.includes('depression') || d.includes('anxiety')) {
    return 'Light daily activity like walking, breathing exercises, and relaxation routines.';
  }
  return 'Light activity as tolerated; stop and seek care if symptoms worsen.';
}

let updatedFields = 0;
for (const [disease, qa] of Object.entries(kb)) {
  const symptoms = qa['What are the symptoms?'] || '';
  const causes = qa['What causes the disease?'] || '';
  const advice = qa['What should a patient do if they have this disease?'] || '';

  if (!qa['Early Stage Symptoms']) {
    qa['Early Stage Symptoms'] = pickPrimarySymptoms(symptoms);
    updatedFields += 1;
  }
  if (!qa['Prevention']) {
    qa['Prevention'] = derivePrevention(disease, causes, advice);
    updatedFields += 1;
  }
  if (!qa['Food']) {
    qa['Food'] = deriveFood(disease);
    updatedFields += 1;
  }
  if (!qa['Exercise']) {
    qa['Exercise'] = deriveExercise(disease);
    updatedFields += 1;
  }
}

fs.writeFileSync(path, `${JSON.stringify(kb, null, 2)}\n`, 'utf8');
console.log(`Updated fields: ${updatedFields}`);
console.log(`Diseases: ${Object.keys(kb).length}`);
