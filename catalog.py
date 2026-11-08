"""Curated ingredient taxonomy for the initial cocktail collection.

Each product has a broad family plus zero or more narrow classifications.
A narrow classification inherits its ancestors. No matching uses string guessing.
"""
TREE = {
    'Spirits': {
        'Gin': {'London Dry Gin': {}, 'Citrus Gin': {}, 'Specialty Gin': {}},
        'Whisky': {'Scotch Whisky': {}, 'Single Malt Whisky': {}, 'Bourbon': {}}, 'Vodka': {}, 'Rum': {'White Rum': {}},
    },
    'Aperitifs & liqueurs': {
        'Bitter aperitivo': {'Italian red bitter': {}},
        'Amaro': {}, 'Cocktail bitters': {'Orange bitters': {}},
        'Limoncello': {}, 'Amaretto': {}, 
    },
    'Fortified wine': {'Vermouth': {'Sweet red vermouth': {}, 'Dry vermouth': {}}},
    'Wine': {'Sparkling wine': {}},
    'Mixers': {'Soda water': {}, 'Tonic water': {'Indian tonic': {}, 'Pepper tonic': {}, 'Mediterranean tonic': {}}, 'Ginger beer': {}},
    'Sweeteners': {'Sugar syrup': {}, 'Brown sugar': {}, 'Elderflower syrup': {}},
    'Fresh ingredients': {'Citrus': {'Orange peel': {}, 'Lemon peel': {}, 'Lemon juice': {}, 'Lime': {}, 'Lime juice': {}}, 'Herbs': {'Mint': {}, 'Basil': {}}, 'Fruit': {}, 'Olives': {}, 'Egg white': {}},
    'Other': {'Ice': {}},
}

# Ingredient label -> most specific applicable categories. Ancestors are inferred.
CLASSIFICATIONS = {
 'Elephant Gin Orange & Cocoa':['London Dry Gin','Citrus Gin'],
 'Tanqueray London Dry Gin':['London Dry Gin'],
 "Hendrick's Gin":['Gin'], 'Seventh Sense Gin':['Gin'], 'Engine Gin':['Gin'], 'Gin':['Gin'],
 'Campari':['Italian red bitter'], 'Select':['Italian red bitter'], 'Tempus Fugit Gran Classico':['Bitter aperitivo'],
 'Cynar':['Amaro'], 'Pimm\'s':['Aperitifs & liqueurs'],
 'Carpano Antica Formula':['Sweet red vermouth'],
 'Carpano Antica Formula / Cocchi/Dos Deus':['Sweet red vermouth'],
 'Giuseppe Giusti Vermouth':['Sweet red vermouth'], 'Dry Vermouth':['Dry vermouth'],
 'Disaronno':['Amaretto'], 'Limoncello Amamalfi':['Limoncello'], 'Aperol':['Bitter aperitivo'],
 'White rum':['White Rum'], 'Whiskey':['Whisky'], 'Vodka':['Vodka'],
 'Sparkling wine / prosecco':['Sparkling wine'],
 'Soda Water':['Soda water'], 'Indian Tonic':['Indian tonic'],
 'Schweppes Pink Pepper Tonic':['Pepper tonic'],
 'Swiss Mountain Spring Provencale Tonic':['Mediterranean tonic'], 'Tribute Tonic':['Tonic water'],
 'Ginger Beer':['Ginger beer'], 'Schweppes Ginger Beer & Chili':['Ginger beer'],
 'Sugar syrup':['Sugar syrup'], 'Brown sugar':['Brown sugar'], 'Elderflower syrup':['Elderflower syrup'],
 'Lemon juice':['Lemon juice'], 'Lime juice (half a lime)':['Lime juice'],
 'Lime (4 pieces)':['Lime'], 'Lime':['Lime'], 'Orange Twist':['Orange peel'], 'Lemon twist':['Lemon peel'],
 'Mint':['Mint'], 'Mint leaves':['Mint'], 'Peppermint':['Mint'], 'Basil leaves':['Basil'],
 'Fruit pieces':['Fruit'], 'Pickled olive':['Olives'], 'Green olive':['Olives'],
 'Cucumber slice':['Fruit'], 'Egg white (optional)':['Egg white'], 'Ice':['Ice'],
 'Angostura Bitter Orange':['Orange bitters'],
}
# An original label can be generic without implying a specific product.
GENERIC_LABELS = {
 'Gin', 'Dry Vermouth', 'Carpano Antica Formula / Cocchi/Dos Deus', 'Sparkling wine / prosecco', 'Soda Water', 'Indian Tonic',
 'Ginger Beer', 'White rum', 'Whiskey', 'Vodka', 'Sugar syrup',
 'Brown sugar', 'Elderflower syrup', 'Lemon juice', 'Lime juice (half a lime)',
 'Lime (4 pieces)', 'Lime', 'Orange Twist', 'Lemon twist',
 'Mint', 'Mint leaves', 'Peppermint', 'Basil leaves', 'Fruit pieces',
 'Pickled olive', 'Green olive', 'Cucumber slice', 'Egg white (optional)', 'Ice'
}
