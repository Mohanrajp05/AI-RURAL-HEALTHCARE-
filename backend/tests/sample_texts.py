"""Static sample texts shared by the translation regression test suite.

Kept in one place so every test file uses the exact same fixtures the
assertions (word-count ranges, required-term lists, etc.) were written
against. Nothing here touches the network or the ML models -- pure string
literals and one small generator function.
"""

# ---------------------------------------------------------------------------
# TEST 1 -- short paragraph (~50-100 words)
# ---------------------------------------------------------------------------
SHORT_PARAGRAPH_EN = (
    "Malaria is a mosquito-borne disease caused by parasites that infect "
    "a certain type of mosquito. People who get malaria are typically very "
    "sick with high fevers, shaking chills, and a flu-like illness. Doctors "
    "diagnose malaria with a blood test and treat it with prescription "
    "medicine. The type of drug and length of treatment depend on the "
    "parasite species, where the person was infected, their age, whether "
    "they are pregnant, and how sick they were when treatment started. "
    "Preventing mosquito bites with repellent and bed nets is the best way "
    "to avoid infection while traveling in areas where malaria is common."
)

# ---------------------------------------------------------------------------
# TEST 2 -- 500-1000 word paragraph (plain long-form health text)
# ---------------------------------------------------------------------------
MEDIUM_PARAGRAPH_EN = """
Diabetes is a chronic health condition that affects how your body turns food into energy. Most of the food you eat is broken down into sugar, also called glucose, and released into your bloodstream. When your blood sugar goes up, it signals your pancreas to release insulin. Insulin acts like a key that lets the blood sugar into your body's cells for use as energy. If you have diabetes, your body either doesn't make enough insulin or can't use the insulin it makes as well as it should. When there isn't enough insulin or cells stop responding to insulin, too much blood sugar stays in your bloodstream, and over time that can cause serious health problems such as heart disease, vision loss, and kidney disease.

There are several types of diabetes, including type 1, type 2, and gestational diabetes, which develops during pregnancy. Type 1 diabetes is caused by an autoimmune reaction where the body attacks itself by mistake, stopping the body from making insulin. About 5 to 10 percent of people with diabetes have type 1, and symptoms often develop quickly. It is usually diagnosed in children, teens, and young adults, and people with type 1 diabetes take insulin every day to survive. Type 2 diabetes is far more common and occurs when the body doesn't use insulin well and is unable to keep blood sugar at normal levels. It develops over many years and is usually diagnosed in adults, though more children and teenagers are being diagnosed with it as well because of rising rates of childhood obesity and sedentary lifestyles.

Common symptoms of diabetes include increased thirst, frequent urination, unexplained weight loss, extreme hunger, presence of ketones in the urine, fatigue, irritability, blurred vision, slow-healing sores, and frequent infections such as gum, skin, or vaginal infections. Many people with type 2 diabetes have no symptoms at all in the early stages, which is why regular screening is important, especially for people who are overweight, have a family history of diabetes, or are over the age of forty five.

Managing diabetes involves a combination of lifestyle changes and, in many cases, medication. Eating a balanced diet rich in vegetables, whole grains, and lean protein while limiting processed sugar and refined carbohydrates can help keep blood sugar levels stable. Regular physical activity, such as walking for thirty minutes a day, improves how the body uses insulin and helps maintain a healthy weight. Monitoring blood sugar levels regularly allows people with diabetes to understand how food, activity, and medication affect their levels, and to make adjustments as needed. For people with type 1 diabetes and some people with type 2, insulin therapy is essential, and it must be carefully dosed and timed with meals.

If left untreated or poorly managed, diabetes can lead to serious complications over time, including damage to the heart and blood vessels, nerves, eyes, and kidneys. High blood sugar levels can damage the inner lining of blood vessels, increasing the risk of heart attack and stroke. Nerve damage, called neuropathy, can cause tingling, numbness, or pain, especially in the hands and feet, and in severe cases can lead to loss of sensation and an increased risk of injury or infection that goes unnoticed. Diabetic retinopathy, a complication affecting the eyes, is one of the leading causes of blindness in adults. Kidney damage, called nephropathy, can eventually lead to kidney failure requiring dialysis or transplant.

Because of these risks, doctors recommend regular checkups for anyone diagnosed with diabetes, including eye exams, foot exams, kidney function tests, and cholesterol screening. Early detection and consistent management make a significant difference in preventing or delaying complications. People at risk of developing type 2 diabetes, including those who are overweight, physically inactive, or have a family history of the disease, can often prevent or delay its onset through weight loss, healthy eating, and regular exercise. Community health workers in rural areas play an important role in educating people about these risk factors and encouraging early screening, since limited access to healthcare facilities can otherwise delay diagnosis until complications have already developed. Support groups and diabetes education programs can also help patients and their families better understand the condition and stick to their treatment plans over the long term, ultimately improving quality of life and reducing the burden of this widespread chronic disease on individuals and healthcare systems alike.
""".strip()


def _build_no_punctuation_paragraph(min_words: int = 520) -> str:
    """Build a long, realistic run-on paragraph with NO sentence-ending
    punctuation (`. ! ? ।`) at all -- the exact shape that originally
    broke chunking (a real symptom description typed on a phone with no
    punctuation), per translation_service._split_words_by_count's docstring.
    Commas are allowed (real punctuation-free dictation still has pauses);
    only sentence terminators are excluded so the sentence-split regex has
    nothing to split on and the word-count fallback must engage.
    """
    clauses = [
        "i have been feeling really tired for the past two weeks",
        "i get headaches almost every day especially in the afternoon",
        "sometimes i feel dizzy when i stand up quickly",
        "my stomach hurts after eating spicy or oily food",
        "i have not been sleeping well at night because i keep waking up",
        "i noticed my hands shake a little when i am hungry",
        "my joints feel stiff in the morning and it takes a while to loosen up",
        "i have been more thirsty than usual and drinking a lot of water",
        "i feel out of breath after climbing just one flight of stairs",
        "there is a dull pain in my lower back that comes and goes",
        "i have lost some weight recently without really trying to",
        "my vision gets blurry sometimes especially when i am tired",
        "i have small sores on my feet that are healing very slowly",
        "i get a racing heartbeat sometimes even when i am just sitting still",
        "my appetite has been low for the last few days",
        "i feel nauseous in the morning before i eat anything",
        "there is some swelling in my ankles by the end of the day",
        "i have been coughing on and off for about a week now",
        "my skin feels itchy in a few different places",
        "i get tingling in my fingertips every now and then",
    ]
    words: list[str] = []
    i = 0
    while len(words) < min_words:
        clause = clauses[i % len(clauses)]
        if i:
            words.append("and")
        words.extend(clause.split())
        i += 1
    return " ".join(words)


# TEST 3 -- 500+ words, deliberately punctuation-free.
NO_PUNCTUATION_PARAGRAPH_EN = _build_no_punctuation_paragraph(520)

# ---------------------------------------------------------------------------
# TEST 4 -- long medical passage covering all the required terms
# ---------------------------------------------------------------------------
MEDICAL_PARAGRAPH_EN = """
Hypertension, also known as high blood pressure, is one of the most common chronic conditions seen in both urban and rural healthcare settings. Blood pressure is measured using two numbers, systolic and diastolic, and a persistent reading above the normal range over multiple visits is generally enough for a diagnosis. Many patients with hypertension have no obvious symptoms at all, which is why it is often called a silent condition, and why regular checkups that measure blood pressure and heart rate are so important even for people who feel completely healthy.

Diabetes is another extremely common chronic condition, and it frequently occurs alongside hypertension in the same patient, which significantly raises the risk of complications. Uncontrolled diabetes damages blood vessels over time in much the same way that uncontrolled hypertension does, and together they are two of the strongest risk factors for a myocardial infarction, commonly known as a heart attack. During a myocardial infarction, blood flow to part of the heart muscle is blocked, usually by a clot, and the affected tissue can be permanently damaged if treatment is delayed, so recognizing the symptoms quickly and getting to a hospital is critical.

Common symptoms that should prompt a patient to seek medical attention include chest pain or pressure, shortness of breath, sudden weakness, and unusual fatigue. A proper diagnosis usually requires a combination of a physical examination, blood tests, an ECG, and in some cases imaging studies, since many of these symptoms overlap with less serious conditions. Once a diagnosis is confirmed, treatment plans are individualized based on the patient's overall health, other existing conditions, and how severe the disease has become. Treatment for hypertension and diabetes typically combines lifestyle changes, such as diet and exercise, with medication when lifestyle changes alone are not enough to bring blood pressure or blood sugar into a safe range.

Medication for these chronic conditions must be taken exactly as prescribed, because taking the wrong dosage, or skipping doses, can allow blood pressure or blood sugar to rise back into a dangerous range without the patient noticing right away. Some medications used for hypertension and heart conditions are also processed by the liver, and the liver's ability to break down these drugs safely should be checked periodically, especially in patients who are on multiple medications at once. Similarly, many common medications, including some used for blood pressure control, are cleared from the body by the kidney, so kidney function tests are an important part of monitoring patients on long-term treatment, since reduced kidney function can cause a drug to build up to unsafe levels even at a normal dosage.

Infection is another factor that can complicate the management of chronic disease. A serious infection can temporarily raise both blood pressure and heart rate, place extra strain on the heart, and in patients with diabetes, can also cause blood sugar to rise sharply, sometimes requiring a temporary change in medication or dosage until the infection clears. Because of all these interactions between hypertension, diabetes, myocardial infarction risk, liver function, kidney function, medication dosage, and infection, doctors generally recommend that patients with any of these chronic conditions attend regular follow-up appointments rather than only seeking care when symptoms become severe, since early adjustments to treatment are far safer and more effective than reacting after a serious complication has already developed.
""".strip()

# ---------------------------------------------------------------------------
# TEST 5 / TEST 6 -- Kannada <-> English long passages (300-800 words)
# ---------------------------------------------------------------------------
# Plain, repeated-with-variation health-awareness sentences in Kannada
# script, long enough to force multi-chunk translation while staying
# straightforward Kannada (short independent clauses) so the source text
# itself is unambiguous for round-trip / language-detection assertions.
_KANNADA_SENTENCES = [
    "ಆರೋಗ್ಯ ಎಂದರೆ ದೇಹ ಮತ್ತು ಮನಸ್ಸು ಎರಡೂ ಚೆನ್ನಾಗಿರುವುದು ಎಂದರ್ಥ.",
    "ಸಕ್ಕರೆ ಕಾಯಿಲೆ ಇರುವವರು ಪ್ರತಿದಿನ ತಮ್ಮ ರಕ್ತದ ಸಕ್ಕರೆ ಮಟ್ಟವನ್ನು ಪರೀಕ್ಷಿಸಬೇಕು.",
    "ಅಧಿಕ ರಕ್ತದೊತ್ತಡ ಇರುವ ರೋಗಿಗಳು ನಿಯಮಿತವಾಗಿ ವೈದ್ಯರನ್ನು ಭೇಟಿಯಾಗಬೇಕು.",
    "ಆರೋಗ್ಯಕರ ಆಹಾರ ಮತ್ತು ದಿನನಿತ್ಯದ ವ್ಯಾಯಾಮ ಹಲವು ರೋಗಗಳನ್ನು ತಡೆಯಲು ಸಹಾಯ ಮಾಡುತ್ತದೆ.",
    "ಸಾಕಷ್ಟು ನೀರು ಕುಡಿಯುವುದು ಮತ್ತು ಚೆನ್ನಾಗಿ ನಿದ್ರೆ ಮಾಡುವುದು ಆರೋಗ್ಯಕ್ಕೆ ಬಹಳ ಮುಖ್ಯ.",
    "ಜ್ವರ, ತಲೆನೋವು ಅಥವಾ ಆಯಾಸ ಮುಂತಾದ ಲಕ್ಷಣಗಳು ಕಂಡುಬಂದರೆ ತಕ್ಷಣ ವೈದ್ಯರನ್ನು ಸಂಪರ್ಕಿಸಬೇಕು.",
    "ಗ್ರಾಮೀಣ ಪ್ರದೇಶಗಳಲ್ಲಿ ಆರೋಗ್ಯ ಕಾರ್ಯಕರ್ತರು ಜನರಿಗೆ ರೋಗ ತಡೆಗಟ್ಟುವಿಕೆಯ ಬಗ್ಗೆ ಶಿಕ್ಷಣ ನೀಡುತ್ತಾರೆ.",
    "ಔಷಧಿಗಳನ್ನು ವೈದ್ಯರ ಸಲಹೆಯಂತೆ ಸರಿಯಾದ ಪ್ರಮಾಣದಲ್ಲಿ ಮಾತ್ರ ತೆಗೆದುಕೊಳ್ಳಬೇಕು.",
    "ಆಸ್ಪತ್ರೆಗೆ ಹೋಗುವ ಮೊದಲು ರೋಗಿಯ ಎಲ್ಲಾ ಲಕ್ಷಣಗಳನ್ನು ಗಮನಿಸುವುದು ಒಳ್ಳೆಯದು.",
    "ಮಕ್ಕಳಿಗೆ ಸಕಾಲದಲ್ಲಿ ಲಸಿಕೆ ಹಾಕಿಸುವುದರಿಂದ ಅನೇಕ ಗಂಭೀರ ಕಾಯಿಲೆಗಳನ್ನು ತಡೆಯಬಹುದು.",
    "ಹೃದಯದ ಆರೋಗ್ಯಕ್ಕಾಗಿ ಉಪ್ಪು ಮತ್ತು ಎಣ್ಣೆಯ ಬಳಕೆಯನ್ನು ಕಡಿಮೆ ಮಾಡುವುದು ಉತ್ತಮ.",
    "ಬಡ ಮತ್ತು ಗ್ರಾಮೀಣ ಕುಟುಂಬಗಳಿಗೆ ಉಚಿತ ಆರೋಗ್ಯ ತಪಾಸಣಾ ಶಿಬಿರಗಳು ಬಹಳ ಸಹಾಯಕವಾಗಿವೆ.",
]


def _build_kannada_paragraph(min_words: int = 340) -> str:
    words: list[str] = []
    i = 0
    while len(words) < min_words:
        words.extend(_KANNADA_SENTENCES[i % len(_KANNADA_SENTENCES)].split())
        i += 1
    return " ".join(words)


LONG_PARAGRAPH_KN = _build_kannada_paragraph(340)

LONG_PARAGRAPH_EN_FOR_KN = """
Good hygiene and clean drinking water are two of the simplest and most effective ways to prevent disease in rural communities. Washing hands with soap before eating and after using the toilet stops the spread of many common infections, including diarrheal diseases that are especially dangerous for young children. Boiling or filtering drinking water removes many harmful organisms that cause waterborne illness, and storing water in clean, covered containers keeps it safe until it is used.

Community health workers play an important role in rural healthcare because they are often the first point of contact for families who cannot easily travel to a hospital or clinic. They educate people about nutrition, vaccination schedules, safe pregnancy practices, and the warning signs that mean a patient needs to see a doctor urgently rather than waiting. Regular vaccination protects children from serious diseases such as measles, tetanus, and whooping cough, and following the recommended schedule is one of the most cost-effective ways to reduce childhood illness and death.

Nutrition also plays a central role in overall health. A balanced diet with enough protein, vitamins, and minerals helps the body fight infection and recover more quickly from illness. Malnutrition in children can lead to long-term physical and cognitive problems, so growth monitoring and nutrition counseling are important parts of routine child healthcare, particularly in areas where food access can be inconsistent across the year.

Finally, early recognition of danger signs saves lives. Persistent high fever, difficulty breathing, severe dehydration, unusual drowsiness, or a wound that will not stop bleeding are all signs that a person needs urgent medical attention rather than home treatment. Teaching families to recognize these warning signs, and making sure they know where the nearest clinic or hospital is located, helps ensure that serious conditions are treated in time instead of becoming life-threatening emergencies.
""".strip()

# ---------------------------------------------------------------------------
# TEST 7 -- long passage packed with numbers, dates, dosages, units
# ---------------------------------------------------------------------------
NUMBERS_MEDICAL_PARAGRAPH_EN = """
The patient is a 72-year-old male who presented to the rural health clinic on 14 March 2026 with complaints of intermittent chest discomfort and shortness of breath over the past 10 days. On examination, his blood pressure was recorded at 150/95 mmHg, his heart rate was 98 bpm, his oxygen saturation was 94%, and his body temperature was 37.5°C. His weight was recorded as 78.5 kg and his height as 1.7 m, giving a body mass index in the overweight range. Blood tests showed a fasting blood sugar of 168 mg/dL and an HbA1c of 8.2%, consistent with poorly controlled type 2 diabetes, which he has had for approximately 12 years.

Given the combination of elevated blood pressure, chest discomfort, and long-standing diabetes, the attending doctor noted that his reading was well above the normal target of 120/80 mmHg, ordered an ECG, and started the patient on Amlodipine 5 mg once daily and Metformin 500 mg twice daily, to be reviewed again after 2 weeks. He was also advised to take Aspirin 75 mg once daily as a preventive measure given his cardiovascular risk factors, along with a 10 mL oral potassium supplement each morning. The nurse recorded that his last hospital admission was in 2023, when he was treated for a similar episode and discharged after 3 days with a follow-up plan that he did not fully complete.

The patient was advised to monitor his blood pressure at home twice daily and to keep a log of the readings, including the date and time of each measurement, so that the values from this week can be compared with those from his next visit on 28 March 2026. He was also given dietary advice to reduce his daily salt intake to below 5 grams per day and to drink at least 2 liters of water daily unless otherwise restricted. A follow-up blood test was scheduled to recheck his HbA1c after 3 months, since a reduction of even 1% in HbA1c is associated with a meaningfully lower risk of long-term complications.

The clinic also noted that approximately 15% of adult patients seen in this region over the past year presented with some combination of hypertension and diabetes, and that early detection through routine blood pressure and blood sugar screening, even a simple check taking less than 10 minutes, remains one of the most effective ways to prevent serious cardiac events such as a myocardial infarction later in life.
""".strip()

# Numeric values that must survive translation somewhere in the output.
# Deliberately excludes generic single digits (e.g. bare "5") that would
# trivially match inside unrelated numbers ("150", "75", "37.5", ...) and
# so wouldn't actually catch a dropped value -- every token here is
# distinctive enough in this paragraph that its absence means real loss.
REQUIRED_NUMERIC_TOKENS = [
    "120/80", "150/95", "98", "37.5", "500", "75", "10",
    "2026", "2023", "15%", "72", "8.2",
]
