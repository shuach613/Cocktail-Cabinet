"""Curated ingredient taxonomy for the imported workbook.

Each product has a broad family plus zero or more narrow classifications.
A narrow classification inherits its ancestors. No matching uses string guessing.
"""
TREE = {
    'Spirits': {
        'Gin': {'London Dry Gin': {}, 'Citrus Gin': {}, 'Contemporary Gin': {}},
        'Whisky': {'Scotch Whisky': {}}, 'Vodka': {}, 'Rum': {'White Rum': {}},
    },
    'Aperitifs & liqueurs': {
        'Bitter aperitivo': {'Italian red bitter': {}},
        'Amaro': {'Artichoke amaro': {}}, 'Cocktail bitters': {'Orange bitters': {}},
        'Limoncello': {}, 'Amaretto': {}, "Pimm’s-style cup liqueur": {},
    },
    'Fortified wine': {'Vermouth': {'Sweet red vermouth': {}, 'Dry vermouth': {}}},
    'Wine': {'Sparkling wine': {'Prosecco': {}}},
    'Mixers': {'Soda water': {}, 'Tonic water': {'Indian tonic': {}, 'Pink pepper tonic': {}, 'Herbal tonic': {}}, 'Ginger beer': {'Spicy ginger beer': {}}},
    'Sweeteners': {'Sugar syrup': {}, 'Brown sugar': {}, 'Elderflower syrup': {}},
    'Fresh ingredients': {'Citrus': {'Orange peel': {}, 'Lemon peel': {}, 'Lemon juice': {}, 'Lime': {}, 'Lime juice': {}}, 'Herbs': {'Mint': {}, 'Basil': {}}, 'Fruit': {}, 'Olives': {}, 'Egg white': {}},
    'Other': {'Ice': {}},
}

# Workbook ingredient label -> most specific applicable tags. Ancestors are inferred.
CLASSIFICATIONS = {
 'Elephant Gin Orange & Cocoa':['London Dry Gin','Citrus Gin'],
 'Tanqueray London Dry Gin':['London Dry Gin'],
 "Hendrick's Gin":['Gin'], 'Seventh Sense Gin':['Gin'], 'Engine Gin':['Gin'], 'Gin':['Gin'],
 'Campari':['Italian red bitter'], 'Select':['Italian red bitter'], 'Tempus Fugit Gran Classico':['Bitter aperitivo'],
 'Cynar':['Artichoke amaro'], 'Pimm\'s':["Pimm’s-style cup liqueur"],
 'Carpano Antica Formula':['Sweet red vermouth'],
 'Carpano Antica Formula / Cocchi/Dos Deus':['Sweet red vermouth'],
 'Giuseppe Giusti Vermouth':['Sweet red vermouth'], 'Dry Vermouth':['Dry vermouth'],
 'Disaronno':['Amaretto'], 'Limoncello Amamalfi':['Limoncello'], 'Apérol':['Bitter aperitivo'],
 'Weisser Rum':['White Rum'], 'Whiskey':['Whisky'], 'Vodka':['Vodka'],
 'Sekt / Prosecco':['Sparkling wine','Prosecco'],
 'Soda Water':['Soda water'], 'Indian Tonic':['Indian tonic'],
 'Schweppes Pink Pepper Tonic':['Pink pepper tonic'],
 'Swiss Moutain Spring Provencale Tonic':['Herbal tonic'], 'Tribute Tonic':['Tonic water'],
 'Ginger Beer':['Ginger beer'], 'Schweppes Ginger Beer & Chili':['Spicy ginger beer'],
 'Zucker Sirup':['Sugar syrup'], 'Brauner Zucker':['Brown sugar'], 'Holunderblütensirup':['Elderflower syrup'],
 'Zitronensaft':['Lemon juice'], 'Limettensaft (halbe Limette)':['Lime juice'],
 'Limette (4 Stücke)':['Lime'], 'Limette':['Lime'], 'Orange Twist':['Orange peel'], 'Zitronen Twist':['Lemon peel'],
 'Minze':['Mint'], 'Minze Blätter':['Mint'], 'Pfefferminze':['Mint'], 'Basilikum Blätter':['Basil'],
 'Fruchtstücke':['Fruit'], 'Olive eingelegt':['Olives'], 'Grüne Olive':['Olives'],
 'Gurkenscheibe':['Fruit'], 'Eiweiss (optional)':['Egg white'], 'Eis':['Ice'],
 'Angostura Bitter Orange':['Orange bitters'],
}
# An original label can be generic without implying a specific product.
GENERIC_LABELS = {
 'Gin', 'Dry Vermouth', 'Carpano Antica Formula / Cocchi/Dos Deus', 'Sekt / Prosecco', 'Soda Water', 'Indian Tonic',
 'Ginger Beer', 'Weisser Rum', 'Whiskey', 'Vodka', 'Zucker Sirup',
 'Brauner Zucker', 'Holunderblütensirup', 'Zitronensaft', 'Limettensaft (halbe Limette)',
 'Limette (4 Stücke)', 'Limette', 'Orange Twist', 'Zitronen Twist',
 'Minze', 'Minze Blätter', 'Pfefferminze', 'Basilikum Blätter', 'Fruchtstücke',
 'Olive eingelegt', 'Grüne Olive', 'Gurkenscheibe', 'Eiweiss (optional)', 'Eis'
}
