#!/usr/bin/env python
# encoding: utf-8

################################################################################
#
#   RMG - Reaction Mechanism Generator
#
#   Copyright (c) 2009-2011 by the RMG Team (rmg_dev@mit.edu)
#
#   Permission is hereby granted, free of charge, to any person obtaining a
#   copy of this software and associated documentation files (the 'Software'),
#   to deal in the Software without restriction, including without limitation
#   the rights to use, copy, modify, merge, publish, distribute, sublicense,
#   and/or sell copies of the Software, and to permit persons to whom the
#   Software is furnished to do so, subject to the following conditions:
#
#   The above copyright notice and this permission notice shall be included in
#   all copies or substantial portions of the Software.
#
#   THE SOFTWARE IS PROVIDED 'AS IS', WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
#   FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
#   DEALINGS IN THE SOFTWARE.
#
################################################################################

"""
This module contains functions for writing of Chemkin input files.
"""

import math
import re
import logging
import os.path
from kinetics import *
from reaction import Reaction
from species import Species
from thermo import NASAPolynomial, NASA
import rmgpy.constants as constants
from quantity import Quantity
from data.base import Entry
from data.kinetics import TemplateReaction, LibraryReaction
from rmg.pdep import PDepReaction
from rmgpy.pdep import LennardJones
from rmgpy.molecule import Molecule

__chemkin_reaction_count = None
    
################################################################################

class ChemkinError(Exception):
    """
    An exception class for exceptional behavior involving Chemkin files. Pass a
    string describing the circumstances that caused the exceptional behavior.
    """
    pass

################################################################################

def readThermoEntry(entry):
    """
    Read a thermodynamics `entry` for one species in a Chemkin file. Returns
    the label of the species and the thermodynamics model as a :class:`NASA`
    object.
    """
    lines = entry.splitlines()
    species = str(lines[0][0:24].split()[0].strip())
        
    # Extract the NASA polynomial coefficients
    # Remember that the high-T polynomial comes first!
    try:
        Tmin = float(lines[0][45:55].strip())
        Tmax = float(lines[0][55:65].strip())
        Tint = float(lines[0][65:75].strip())
    
        a0_high = float(lines[1][0:15].strip())
        a1_high = float(lines[1][15:30].strip())
        a2_high = float(lines[1][30:45].strip())
        a3_high = float(lines[1][45:60].strip())
        a4_high = float(lines[1][60:75].strip())
    
        a5_high = float(lines[2][0:15].strip())
        a6_high = float(lines[2][15:30].strip())
        a0_low = float(lines[2][30:45].strip())
        a1_low = float(lines[2][45:60].strip())
        a2_low = float(lines[2][60:75].strip())
    
        a3_low = float(lines[3][0:15].strip())
        a4_low = float(lines[3][15:30].strip())
        a5_low = float(lines[3][30:45].strip())
        a6_low = float(lines[3][45:60].strip())
    except (IndexError, ValueError):
        raise ChemkinError('Error while reading thermo entry for species {0}'.format(species))
    
    # Construct and return the thermodynamics model
    thermo = NASA(
        polynomials = [
            NASAPolynomial(Tmin=(Tmin,"K"), Tmax=(Tint,"K"), coeffs=[a0_low, a1_low, a2_low, a3_low, a4_low, a5_low, a6_low]),
            NASAPolynomial(Tmin=(Tint,"K"), Tmax=(Tmax,"K"), coeffs=[a0_high, a1_high, a2_high, a3_high, a4_high, a5_high, a6_high])
        ],
        Tmin = (Tmin,"K"),
        Tmax = (Tmax,"K"),
    )

    return species, thermo

################################################################################

def readKineticsEntry(entry, speciesDict, Aunits, Eunits):
    """
    Read a kinetics `entry` for a single reaction as loaded from a Chemkin
    file. The associated mapping of labels to species `speciesDict` should also
    be provided. Returns a :class:`Reaction` object with the reaction and its
    associated kinetics.
    """
    Afactor = {
        'cm^3/(mol*s)': 1.0e6,
        'cm^3/(molecule*s)': 1.0e6,
        'm^3/(mol*s)': 1.0,
        'm^3/(molecule*s)': 1.0,
    }[Aunits[2]]
    
    lines = entry.strip().splitlines()
    
    # The first line contains the reaction equation and a set of modified Arrhenius parameters
    tokens = lines[0].split()
    
    rmg = True
    try:
        float(tokens[-6])
    except (ValueError, IndexError):
        rmg = False
    AuncertaintyType = '+|-'
    if rmg:
        A = float(tokens[-6])
        n = float(tokens[-5])
        Ea = float(tokens[-4])
        try:
            dA = float(tokens[-3])
        except ValueError:
            AuncertaintyType = '*|/'
            dA = float(tokens[-3][1:])
        dn = float(tokens[-2])
        dEa = float(tokens[-1])
        reaction = ''.join(tokens[:-6])
    else:
        A = float(tokens[-3])
        n = float(tokens[-2])
        Ea = float(tokens[-1])
        dA = 0.0
        dn = 0.0
        dEa = 0.0
        reaction = ''.join(tokens[:-3])
    thirdBody = False
    
    # Split the reaction equation into reactants and products
    reversible = True
    reactants, products = reaction.split('=')
    if '<=>' in reaction:
        reactants = reactants[:-1]
        products = products[1:]
    elif '=>' in reaction:
        products = products[1:]
        reversible = False
    if '(+M)' in reactants: reactants = reactants.replace('(+M)','')
    if '(+m)' in reactants: reactants = reactants.replace('(+m)','')
    if '(+M)' in products:  products = products.replace('(+M)','')
    if '(+m)' in products:  products = products.replace('(+m)','')
    
    # Create a new Reaction object for this reaction
    reaction = Reaction(reactants=[], products=[], reversible=reversible)
    
    # Convert the reactants and products to Species objects using the speciesDict
    for reactant in reactants.split('+'):
        reactant = reactant.strip().upper()
        stoichiometry = 1
        if reactant[0].isdigit():
            # This allows for reactions to be of the form 2A=B+C instead of A+A=B+C
            # The implementation below assumes an integer between 0 and 9, inclusive
            stoichiometry = int(reactant[0])
            reactant = reactant[1:]               
        if reactant.upper() == 'M':
            thirdBody = True
        elif reactant not in speciesDict:
            raise ChemkinError('Unexpected reactant "{0}" in reaction {1}.'.format(reactant, reaction))
        else:
            for i in range(stoichiometry):
                reaction.reactants.append(speciesDict[reactant])
    for product in products.split('+'):
        product = product.strip().upper()
        stoichiometry = 1
        if product[0].isdigit():
            # This allows for reactions to be of the form A+B=2C instead of A+B=C+C
            # The implementation below assumes an integer between 0 and 9, inclusive
            stoichiometry = int(product[0])
            product = product[1:]
        if product.upper() == 'M':
            pass
        elif product not in speciesDict:
            raise ChemkinError('Unexpected product "{0}" in reaction {1}.'.format(product, reaction))
        else:
            for i in range(stoichiometry):
                reaction.products.append(speciesDict[product])
    
    # Determine the appropriate units for k(T) and k(T,P) based on the number of reactants
    # This assumes elementary kinetics for all reactions
    try:
        Nreac = len(reaction.reactants) + (1 if thirdBody else 0)
        kunits = Aunits[Nreac]
        klow_units = Aunits[Nreac+1]
    except IndexError:
        raise ChemkinError('Invalid number of reactant species for reaction {0}.'.format(reaction))
    
    # The rest of the first line contains the high-P limit Arrhenius parameters (if available)
    #tokens = lines[0][52:].split()
    tokens = lines[0].split()[1:]
    arrheniusHigh = Arrhenius(
        A = (A,kunits,AuncertaintyType,dA),
        n = (n,'','+|-',dn),
        Ea = (Ea,Eunits,'+|-',dEa),
        T0 = (1,"K"),
    )
    
    if len(lines) == 1 and not thirdBody:
        # If there's only one line then we know to use the high-P limit kinetics as-is
        reaction.kinetics = arrheniusHigh
    else:
        # There's more kinetics information to be read
        arrheniusLow = None
        troe = None
        lindemann = None
        chebyshev = None
        pdepArrhenius = None
        efficiencies = {}
        chebyshevCoeffs = []
        
    
        # Note that the subsequent lines could be in any order
        for line in lines[1:]:
            line = line.upper()
            tokens = line.split('/')
            if 'DUP' in line:            
                # Duplicate reaction
                reaction.duplicate = True
            
            elif 'LOW' in line:
                # Low-pressure-limit Arrhenius parameters
                tokens = tokens[1].split()
                arrheniusLow = Arrhenius(
                    A = (float(tokens[0].strip()),klow_units),
                    n = float(tokens[1].strip()),
                    Ea = (float(tokens[2].strip()),Eunits),
                    T0 = (1,"K"),
                )
            
            elif 'HIGH' in line:
                # High-pressure-limit Arrhenius parameters
                tokens = tokens[1].split()
                arrheniusLow = arrheniusHigh
                arrheniusLow.A = (arrheniusLow.A.value,klow_units)
                arrheniusHigh = Arrhenius(
                    A = (float(tokens[0].strip()),kunits),
                    n = float(tokens[1].strip()),
                    Ea = (float(tokens[2].strip()),Eunits),
                    T0 = (1,"K"),
                )

            elif 'TROE' in line:
                # Troe falloff parameters
                tokens = tokens[1].split()
                alpha = float(tokens[0].strip())
                T3 = float(tokens[1].strip())
                T1 = float(tokens[2].strip())
                try:
                    T2 = float(tokens[3].strip())
                except (IndexError, ValueError):
                    T2 = None
                
                troe = Troe(
                    alpha = alpha,
                    T3 = (T3,"K"),
                    T1 = (T1,"K"),
                    T2 = (T2,"K") if T2 is not None else None,
                )
            
            elif 'CHEB' in line:
                # Chebyshev parameters
                if chebyshev is None:
                    chebyshev = Chebyshev()
                    chebyshev.kunits = kunits
                tokens = [t.strip() for t in tokens]
                if 'TCHEB' in line:
                    index = tokens.index('TCHEB')
                    tokens2 = tokens[index+1].split()
                    chebyshev.Tmin = Quantity(float(tokens2[0].strip()),"K")
                    chebyshev.Tmax = Quantity(float(tokens2[1].strip()),"K")
                if 'PCHEB' in line:
                    index = tokens.index('PCHEB')
                    tokens2 = tokens[index+1].split()
                    chebyshev.Pmin = Quantity(float(tokens2[0].strip()),"atm")
                    chebyshev.Pmax = Quantity(float(tokens2[1].strip()),"atm")
                if 'TCHEB' in line or 'PCHEB' in line:
                    pass
                elif chebyshev.degreeT == 0 or chebyshev.degreeP == 0:
                    tokens2 = tokens[1].split()
                    chebyshev.degreeT = int(float(tokens2[0].strip()))
                    chebyshev.degreeP = int(float(tokens2[1].strip()))
                    chebyshev.coeffs = numpy.zeros((chebyshev.degreeT,chebyshev.degreeP), numpy.float64)
                else:
                    tokens2 = tokens[1].split()
                    chebyshevCoeffs.extend([float(t.strip()) for t in tokens2])
                    
            elif 'PLOG' in line:
                # Pressure-dependent Arrhenius parameters
                if pdepArrhenius is None:
                    pdepArrhenius = []
                tokens = tokens[1].split()
                pdepArrhenius.append([float(tokens[0].strip()), Arrhenius(
                    A = (float(tokens[1].strip()),kunits),
                    n = float(tokens[2].strip()),
                    Ea = (float(tokens[3].strip()),Eunits),
                    T0 = (1,"K"),
                )])

            else:
                # Assume a list of collider efficiencies
                try:
                    for collider, efficiency in zip(tokens[0::2], tokens[1::2]):
                        efficiencies[speciesDict[collider.strip().upper()].molecule[0]] = float(efficiency.strip())
                except IndexError:
                    error_msg = 'Could not read collider efficiencies for reaction: {0}.\n'.format(reaction)
                    error_msg += 'The following line was parsed incorrectly:\n{0}'.format(line)
                    raise ChemkinError(error_msg)

        # Decide which kinetics to keep and store them on the reaction object
        # Only one of these should be true at a time!
        if chebyshev is not None:
            if chebyshev.Tmin is None or chebyshev.Tmax is None:
                raise ChemkinError('Missing TCHEB line for reaction {0}'.format(reaction))
            if chebyshev.Pmin is None or chebyshev.Pmax is None:
                raise ChemkinError('Missing PCHEB line for reaction {0}'.format(reaction))
            index = 0
            for t in range(chebyshev.degreeT):
                for p in range(chebyshev.degreeP):
                    chebyshev.coeffs.value_si[t,p] = chebyshevCoeffs[index]
                    index += 1
            # Don't forget to convert the Chebyshev coefficients to SI units!
            # This assumes that s^-1, cm^3/mol*s, etc. are compulsory
            chebyshev.coeffs.value_si[0,0] -= (len(reaction.reactants) - 1) * math.log10(Afactor)
            reaction.kinetics = chebyshev
        elif pdepArrhenius is not None:
            reaction.kinetics = PDepArrhenius(
                pressures = ([P for P, arrh in pdepArrhenius],"atm"),
                arrhenius = [arrh for P, arrh in pdepArrhenius],
            )
        elif troe is not None:
            troe.arrheniusHigh = arrheniusHigh
            troe.arrheniusLow = arrheniusLow
            troe.efficiencies = efficiencies
            reaction.kinetics = troe
        elif arrheniusLow is not None:
            reaction.kinetics = Lindemann(arrheniusHigh=arrheniusHigh, arrheniusLow=arrheniusLow)
            reaction.kinetics.efficiencies = efficiencies
        elif thirdBody:
            reaction.kinetics = ThirdBody(arrheniusLow=arrheniusHigh)
            reaction.kinetics.efficiencies = efficiencies
        elif reaction.duplicate:
            reaction.kinetics = arrheniusHigh
        else:
            raise ChemkinError('Unable to determine pressure-dependent kinetics for reaction {0}.'.format(reaction))
   
    return reaction

def readReactionComments(reaction, comments, read = True):
    """
    Parse the `comments` associated with a given `reaction`. If the comments
    come from RMG (Py or Java), parse them and extract the useful information.
    Return the reaction object based on the information parsed from these
    comments. If `read` if False, the reaction is returned as an "Unclassified"
    LibraryReaction.
    """
    from rmgpy.data.kinetics import LibraryReaction,TemplateReaction, KineticsLibrary, KineticsFamily
    from rmgpy.rmg.pdep import PDepReaction, PDepNetwork
    
    if read == False:
        # The chemkin file was not generated by either RMG-Py or RMG-Java, thus, there should be no parsing
        # of the comments.  Instead, return as an unclassified LibraryReaction.
        reaction = LibraryReaction(
            index = reaction.index,
            reactants = reaction.reactants, 
            products = reaction.products, 
            kinetics = reaction.kinetics,
            reversible = reaction.reversible,
            duplicate = reaction.duplicate,
            library = KineticsLibrary(label='Unclassified'),
        )        
        
        return reaction  
    
    atKineticsComments = False
    lines = comments.strip().splitlines()
        
    for line in lines:
        
        
        tokens = line.split()
        if 'Reaction index:' in line:
            # Don't store the reaction indices
            pass
        
        elif 'Template reaction:' in line:
            label = str(tokens[-2])
            template = tokens[-1][1:-1].split(',')
            reaction = TemplateReaction(
                index = reaction.index,
                reactants = reaction.reactants, 
                products = reaction.products, 
                kinetics = reaction.kinetics,
                reversible = reaction.reversible,
                duplicate = reaction.duplicate,
                family = KineticsFamily(label=label),
                template = [Entry(label=g) for g in template],
            )
            
        elif 'Library reaction:' in line or 'Seed mechanism:' in line:
            label = str(tokens[-1])
            reaction = LibraryReaction(
                index = reaction.index,
                reactants = reaction.reactants, 
                products = reaction.products, 
                kinetics = reaction.kinetics,
                reversible = reaction.reversible,
                duplicate = reaction.duplicate,
                library = KineticsLibrary(label=label),
            )   
            
        elif 'PDep reaction:' in line:
            networkIndex = int(tokens[-1][1:])
            reaction = PDepReaction(
                index = reaction.index,
                reactants = reaction.reactants, 
                products = reaction.products, 
                kinetics = reaction.kinetics, 
                reversible = reaction.reversible,
                duplicate = reaction.duplicate,
                network = PDepNetwork(index=networkIndex), 
            )
            
        elif 'Flux pairs:' in line:
            reaction.pairs = []
            for reacStr, prodStr in zip(tokens[2::2], tokens[3::2]):
                if reacStr[-1] == ',': reacStr = reacStr[:-1]
                for reactant in reaction.reactants:
                    if reactant.label == reacStr:
                        break
                else:
                    import pdb; pdb.set_trace()
                    raise ChemkinError('Unexpected species identifier {0} encountered in flux pairs for reaction {1}.'.format(reacStr, reaction))
                if prodStr[-1] == ';': prodStr = prodStr[:-1]
                for product in reaction.products:
                    if product.label == prodStr:
                        break
                else:
                    import pdb; pdb.set_trace()
                    raise ChemkinError('Unexpected species identifier {0} encountered in flux pairs for reaction {1}.'.format(prodStr, reaction))
                reaction.pairs.append((reactant, product))
            assert len(reaction.pairs) == max(len(reaction.reactants), len(reaction.products))

        elif 'Kinetics comments:' in line:
            atKineticsComments = True

        elif atKineticsComments:
            reaction.kinetics.comment += line.strip() + "\n"


        # Comment parsing from old RMG-Java chemkin files
        elif 'PDepNetwork' in line:
            networkIndex = int(tokens[3][1:])
            reaction = PDepReaction(
                index = reaction.index,
                reactants = reaction.reactants, 
                products = reaction.products,
                kinetics = reaction.kinetics,
                reversible = reaction.reversible,
                duplicate = reaction.duplicate,
                network = PDepNetwork(index=networkIndex)
                )
            reaction.kinetics.comment = line

        elif 'ReactionLibrary:' in line or 'Seed Mechanism:' in line:
            label = str(tokens[-1])
            reaction = LibraryReaction(
                index = reaction.index,
                reactants = reaction.reactants, 
                products = reaction.products, 
                kinetics = reaction.kinetics,
                reversible = reaction.reversible,
                duplicate = reaction.duplicate,
                library = KineticsLibrary(label=label),
            )
            reaction.kinetics.comment = line
            
        elif 'exact' in line or 'estimate' in line:
            index1 = line.find('[')
            index2 = line.find(']')
            template = [s.strip() for s in line[index1:index2].split(',')]
            label = str(tokens[0])
            reaction = TemplateReaction(
                index = reaction.index,
                reactants = reaction.reactants, 
                products = reaction.products, 
                kinetics = reaction.kinetics,
                reversible = reaction.reversible,
                duplicate = reaction.duplicate,
                family = KineticsFamily(label=label),
                template = [Entry(label=g) for g in template],
            )
            reaction.kinetics.comment = line

    if not isinstance(reaction, LibraryReaction) and not isinstance(reaction, TemplateReaction) and not isinstance(reaction,PDepReaction):
        reaction = LibraryReaction(
            index = reaction.index,
            reactants = reaction.reactants, 
            products = reaction.products, 
            kinetics = reaction.kinetics,
            reversible = reaction.reversible,
            duplicate = reaction.duplicate,
            library = KineticsLibrary(label='Unclassified'),
        )  
            
    return reaction

################################################################################

def loadSpeciesDictionary(path):
    """
    Load an RMG dictionary - containing species identifiers and the associated
    adjacency lists - from the file located at `path` on disk. Returns a dict
    mapping the species identifiers to the loaded species. Resonance isomers
    for each species are automatically generated.
    """
    speciesDict = {}
    
    with open(path, 'r') as f:
        adjlist = ''
        for line in f:
            if line.strip() == '' and adjlist.strip() != '':
                # Finish this adjacency list
                species = Species().fromAdjacencyList(adjlist)
                species.generateResonanceIsomers()
                label = species.label.upper()
                speciesDict[label] = species
                adjlist = ''
            else:
                if "InChI" in line:
                    line = line.split()[0] + '\n'
                if '//' in line:
                    index = line.index('//')
                    line = line[0:index]
                adjlist += line

    return speciesDict

def removeCommentFromLine(line):
    """
    Remove a comment from a line of a Chemkin file or species dictionary file.
    """
    try:
        index1 = line.index('!')
    except ValueError:
        index1 = len(line)
    try:
        index2 = line.index('//')
    except ValueError:
        index2 = len(line)
    
    index = min(index1, index2)
    comment = line[index+1:-1]
    if index < len(line):
        line = line[0:index] + '\n'
    return line, comment

def loadTransportFile(path, speciesDict):
    """
    Load a Chemkin transport properties file located at `path` and store the
    properties on the species in `speciesDict`.
    """
    with open(path, 'r') as f:
        for line0 in f:
            line = removeCommentFromLine(line0)[0]
            line = line.strip()
            if line != '':
                # This line contains an entry, so parse it
                label = line[0:16].upper().strip()
                data = line[16:].split()
                species = speciesDict[label]
                species.lennardJones = LennardJones(
                    sigma = (float(data[2]),'angstrom'),
                    epsilon = (float(data[1]),'K'),
                )
                species.dipoleMoment = (float(data[3]),'De')
                species.polarizability = (float(data[4]),'angstrom^3')
                species.Zrot = (float(data[5]),'')

def loadChemkinFile(path, dictionaryPath=None, transportPath=None, readComments = True):
    """
    Load a Chemkin input file to `path` on disk, returning lists of the species
    and reactions in the Chemkin file.
    """
    
    speciesList = []; speciesDict = {}; speciesAliases = {}
    reactionList = []

    # If the dictionary path is given, the read it and generate Molecule objects
    # You need to append an additional adjacency list for nonreactive species, such
    # as N2, or else the species objects will not store any structures for the final
    # HTML output.
    if dictionaryPath:
        speciesDict = loadSpeciesDictionary(dictionaryPath)
    
    with open(path, 'r') as f:
    
        line0 = f.readline()
        while line0 != '':        
            line = removeCommentFromLine(line0)[0]
            line = line.strip()
            tokens = line.split()
            tokens_upper = line.upper().split()
            
            if 'SPECIES' in line.upper():
                # List of species identifiers
                index = tokens_upper.index('SPECIES')
                tokens = tokens[index+1:]
                while 'END' not in tokens_upper:
                    line = f.readline()
                    # If the line contains only one species, and also contains
                    # a comment with only one token, assume that token is 
                    # intended to be the true identifier for the species, but
                    # was not used e.g. due to a length limitation
                    if '!' in line and len(line.split('!')) == 2:
                        label, alias = line.split('!')
                        label = label.strip()
                        alias = alias.strip()
                        if len(label.split()) == 1 and len(alias.split()) == 1:
                            speciesAliases[label] = alias
                    line = removeCommentFromLine(line)[0]
                    line = line.strip()
                    tokens.extend(line.split())
                    tokens_upper.extend(line.upper().split())
                
                for token in tokens:
                    token_upper = token.upper()
                    if token_upper == 'END':
                        break
                    if token_upper in speciesDict:
                        species = speciesDict[token_upper]
                    else:
                        species = Species(label=token)
                        speciesDict[token_upper] = species
                    speciesList.append(species)
                
                # Also always add in a few bath gases (since RMG-Java does)
                for label, smiles in [('Ar','[Ar]'), ('He','[He]'), ('Ne','[Ne]'), ('N2','N#N')]:
                    molecule = Molecule().fromSMILES(smiles)
                    for species in speciesList:
                        if species.label == label:
                            if len(species.molecule) == 0:
                                species.molecule = [molecule]
                            break
                        if species.isIsomorphic(molecule):
                            break
                    else:
                        species = Species(label=label, molecule=[molecule])
                        speciesList.append(species)
                        speciesDict[label.upper()] = species                            
                
            elif 'THERM' in line.upper():
                # List of thermodynamics (hopefully one per species!)
                line = f.readline()
                thermo = ''
                comments = ''
                while line != '' and 'END' not in line.upper():
                    line, comment = removeCommentFromLine(line)
                    if comment: comments += comment.strip().replace('\t',', ') + '\n'
                    if len(line) >= 80:
                        if line[79] in ['1', '2', '3', '4']:
                            thermo += line
                            if line[79] == '4':
                                label, thermo = readThermoEntry(thermo)
                                label = label.upper()
                                try:
                                    speciesDict[label].thermo = thermo
                                    speciesDict[label].thermo.comment = comments
                                    comments = ''
                                except KeyError:
                                    if label in ['AR', 'N2', 'HE', 'NE']:
                                        pass
                                    else:
                                        logging.warning('Skipping unexpected species "{0}" while reading thermodynamics entry.'.format(label))
                                thermo = ''
                    line = f.readline()
                
            elif 'REACTIONS' in line.upper():
                # Reactions section
                # Unread the line (we'll re-read it in readReactionBlock())
                f.seek(-len(line0), 1)
                reactionList = readReactionsBlock(f, speciesDict, readComments = readComments)
                    
            line0 = f.readline()
            
    # Index the reactions now to have identical numbering as in Chemkin 
    index = 0
    for reaction in reactionList:
        index += 1
        reaction.index = index

    # Check for marked (and unmarked!) duplicate reactions
    # Combine marked duplicate reactions into a single reaction using MultiKinetics
    # Raise exception for unmarked duplicate reactions
    duplicateReactionsToRemove = []
    duplicateReactionsToAdd = []
    for index1 in range(len(reactionList)):
        reaction1 = reactionList[index1]
        if reaction1 in duplicateReactionsToRemove:
            continue

        for index2 in range(index1+1, len(reactionList)):
            reaction2 = reactionList[index2]
            if reaction1.reactants == reaction2.reactants and reaction1.products == reaction2.products:
                if reaction1.duplicate and reaction2.duplicate:

                    for reaction in duplicateReactionsToAdd:
                        if reaction1.reactants == reaction.reactants and reaction1.products == reaction.products:
                            break
                    
                    if isinstance(reaction1, LibraryReaction) and isinstance(reaction2, LibraryReaction):
                        assert reaction1.library.label == reaction2.library.label
                        if isinstance(reaction1.kinetics, PDepArrhenius):
                            kinetics = MultiPDepArrhenius()
                        elif isinstance(reaction1.kinetics, Arrhenius):
                            kinetics = MultiArrhenius()
                        else:
                            raise ChemkinError('Unexpected kinetics type {0} for duplicate reaction {1}.'.format(reaction1.kinetics.__class__, reaction1))
                        reaction = LibraryReaction(
                            index = reaction1.index,
                            reactants = reaction1.reactants,
                            products = reaction1.products,
                            kinetics = kinetics,
                            library = reaction1.library,
                            duplicate = False,
                        )
                        duplicateReactionsToAdd.append(reaction)
                        kinetics.arrhenius = [reaction1.kinetics]
                        duplicateReactionsToRemove.append(reaction1)

                    else:
                        # Do not use as duplicate reactions if it's not a library reaction
                        # Template reactions should be kept separate
                        continue
                    
                    if isinstance(reaction.kinetics, MultiPDepArrhenius) and isinstance(reaction2.kinetics, PDepArrhenius):
                        reaction.kinetics.arrhenius.append(reaction2.kinetics)
                    elif isinstance(reaction.kinetics, MultiArrhenius) and isinstance(reaction2.kinetics, Arrhenius):
                        reaction.kinetics.arrhenius.append(reaction2.kinetics)
                    else:
                        raise ChemkinError('Mixed kinetics for duplicate reaction {0}.'.format(reaction))
                    
                    duplicateReactionsToRemove.append(reaction2)
                elif reaction1.kinetics.isPressureDependent() == reaction2.kinetics.isPressureDependent():
                    # If both reactions are pressure-independent or both are pressure-dependent, then they need duplicate tags
                    # Chemkin treates pdep and non-pdep reactions as different, so those are okay
                    raise ChemkinError('Encountered unmarked duplicate reaction {0}.'.format(reaction1))
                    
    for reaction in duplicateReactionsToRemove:
        reactionList.remove(reaction)
    reactionList.extend(duplicateReactionsToAdd)

    # If the transport path is given, then read it to obtain the transport
    # properties
    if transportPath:
        loadTransportFile(transportPath, speciesDict)
    
    # Apply species aliases if known
    for spec in speciesList:
        try:
            spec.label = speciesAliases[spec.label]
        except KeyError:
            pass
    
    # Attempt to extract index from species label
    indexPattern = re.compile(r'\(\d+\)$')
    for spec in speciesList:
        if indexPattern.search(spec.label):
            label, sep, index = spec.label[:-1].rpartition('(')
            spec.label = label
            spec.index = int(index)

    reactionList.sort(key=lambda reaction: reaction.index)
    return speciesList, reactionList

def readReactionsBlock(f, speciesDict, readComments = True):
    """
    Read a reactions block from a Chemkin file stream.
    
    This function can also read the ``reactions.txt`` and ``pdepreactions.txt``
    files from RMG-Java kinetics libraries, which have a similar syntax.
    """    
    energyUnits = 'cal/mol'
    moleculeUnits = 'moles'
    volumeUnits = 'cm3'
    timeUnits = 's'
    
    line = f.readline()
    found = False
    while line != '' and not found:
    
        line = removeCommentFromLine(line)[0]
        line = line.strip()
        tokens = line.split()
        
        if len(tokens) > 0 and tokens[0].upper() == 'REACTIONS':
            # Regular Chemkin file
            found = True
            try:
                energyUnits = tokens[1].lower()
                moleculeUnits = tokens[2].lower()
            except IndexError:
                pass

        elif len(tokens) > 0 and tokens[0].lower() == 'unit:':
            # RMG-Java kinetics library file
            found = True
            while 'reactions:' not in line.lower():
                line = f.readline()
                line = removeCommentFromLine(line)[0]
                line = line.strip()
                
                if 'A:' in line or 'E:' in line:
                    units = line.split()[1]
                    if 'A:' in line:
                        moleculeUnits, volumeUnits, timeUnits = units.lower().split('/') # Assume this is a 3-tuple: moles or molecules, volume, time
                    elif 'E:' in line:
                        energyUnits = units.lower()
        else:
            line = f.readline()
            
    if not found:
        raise ChemkinError('Invalid reaction block.')
    
    # Check that the units are valid
    assert moleculeUnits in ['molecules', 'moles', 'mole', 'mol', 'molecule']
    assert volumeUnits in ['cm3', 'm3']
    assert timeUnits in ['s']
    assert energyUnits in ['kcal/mole', 'kcal/mol', 'cal/mole', 'cal/mol', 'kj/mole', 'kj/mol', 'j/mole', 'j/mol']
    
    # Homogenize units
    if moleculeUnits == 'molecules':
        moleculeUnits = 'molecule'
    elif moleculeUnits == 'moles' or moleculeUnits == 'mole':
        moleculeUnits = 'mol'
    volumeUnits = {'cm3': 'cm', 'm3': 'm'}[volumeUnits]
    if energyUnits == 'kcal/mole':
        energyUnits = 'kcal/mol'
    elif energyUnits == 'cal/mole':
        energyUnits = 'cal/mol'
    elif energyUnits == 'kj/mole':
        energyUnits = 'kj/mol'
    elif energyUnits == 'j/mole':
        energyUnits = 'j/mol'
    energyUnits = energyUnits.replace('j/mol', 'J/mol')
    
    # Set up kinetics units
    Aunits = [
        '',                                                                 # Zeroth-order
        's^-1'.format(timeUnits),                                           # First-order
        '{0}^3/({1}*{2})'.format(volumeUnits, moleculeUnits, timeUnits),    # Second-order
        '{0}^6/({1}^2*{2})'.format(volumeUnits, moleculeUnits, timeUnits),  # Third-order
        '{0}^9/({1}^3*{2})'.format(volumeUnits, moleculeUnits, timeUnits),  # Fourth-order
    ]
    Eunits = energyUnits
    
    kineticsList = []
    commentsList = []
    kinetics = ''
    comments = ''
    
    line = f.readline()
    while line != '':
        
        lineStartsWithComment = line.startswith('!') or line.startswith('//') 
        line, comment = removeCommentFromLine(line)
        line = line.strip(); comment = comment.strip()
    
        if 'end' in line or 'END' in line:
            break
    
        if 'rev' in line or 'REV' in line:
            # can no longer name reactants rev...
            line = f.readline()

        if '=' in line and not lineStartsWithComment:
            # Finish previous record
            kineticsList.append(kinetics)
            commentsList.append(comments)
            kinetics = ''
            comments = ''
            
        if line: kinetics += line + '\n'
        if comment: comments += comment + '\n'
        
        line = f.readline()
        
    # Don't forget the last reaction!
    if kinetics.strip() != '':
        kineticsList.append(kinetics)
        commentsList.append(comments)
    
    if len(kineticsList) == 0 and len(commentsList) == 0:
        # No reactions found
        pass
    elif kineticsList[0] == '' and commentsList[-1] == '':
        # True for Chemkin files generated from RMG-Py
        kineticsList.pop(0)
        commentsList.pop(-1)
    elif kineticsList[0] == '' and commentsList[0] == '':
        # True for Chemkin files generated from RMG-Java
        kineticsList.pop(0)
        commentsList.pop(0)
    else:
        # In reality, comments can occur anywhere in the Chemkin
        # file (e.g. either or both of before and after the
        # reaction equation)
        # If we can't tell what semantics we are using, then just
        # throw the comments away
        # (This is better than failing to load the Chemkin file at
        # all, which would likely occur otherwise)
        if kineticsList[0] == '':
            kineticsList.pop(0)
        if len(kineticsList) != len(commentsList):
            commentsList = ['' for kinetics in kineticsList]
        
    reactionList = []
    for kinetics, comments in zip(kineticsList, commentsList):
        reaction = readKineticsEntry(kinetics, speciesDict, Aunits, Eunits)
        reaction = readReactionComments(reaction, comments, read = readComments)
        reactionList.append(reaction)
        
    return reactionList

################################################################################

def saveHTMLFile(path, readComments = True):
    """
    Save an output HTML file from the contents of a RMG-Java output folder
    """
    from rmgpy.rmg.model import CoreEdgeReactionModel
    from rmgpy.rmg.output import saveOutputHTML
    chemkinPath= path + '/chemkin/chem.inp'
    dictionaryPath = path + 'RMG_Dictionary.txt'
    model = CoreEdgeReactionModel()
    model.core.species, model.core.reactions = loadChemkinFile(chemkinPath,dictionaryPath, readComments = readComments)
    outputPath = path + 'output.html'
    speciesPath = path + '/species/'
    if not os.path.isdir(speciesPath):
        os.makedirs(speciesPath)
    saveOutputHTML(outputPath, model)

################################################################################
def getSpeciesIdentifier(species):
    """
    Return a string identifier for the provided `species` that can be used in a
    Chemkin file. Although the Chemkin format allows up to 16 characters for a
    species identifier, this function uses a maximum of 10 to ensure that all
    reaction equations fit in the maximum limit of 52 characters.
    """

    # Special case for inert colliders - just use the label if possible
    if not species.reactive and 0 < len(species.label) <= 10:
        return species.label

    # The algorithm is slightly different depending on whether or not the
    # species has an index
    # If so, we want to include the index in the identifier
    if species.index == -1:
        # No index present -- probably not in RMG job
        # In this case just return the label (if the right size)
        if len(species.label) > 0 and not re.search(r'[^A-Za-z0-9\-_,\(\)\*]+', species.label):
            if len(species.label) <= 10:
                return species.label
            elif len(species.label) <= 15:
                logging.warning('Species label is longer than 10 characters and may exceed chemkin string limit')
                return species.label            
    else:
        
        # Index present - the index will be included in the identifier
        # (at the expense of the current label or formula if need be)

        # First try to use the label and index
        # The label can only contain alphanumeric characters, hyphens, and underscores
        if len(species.label) > 0 and species.index >= 0 and not re.search(r'[^A-Za-z0-9\-_,\(\)\*]+', species.label):
            name = '{0}({1:d})'.format(species.label, species.index)
            if len(name) <= 10:
                return name
    
        # Next try the chemical formula
        if len(species.molecule) > 0:
            # Try the chemical formula
            name = '{0}({1:d})'.format(species.molecule[0].getFormula(), species.index)
            if len(name) <= 10:
                return name
    
        # As a last resort, just use the index
        if species.index >= 0:
            name = 'S({0:d})'.format(species.index)
            if len(name) <= 10:
                return name

    # If we're here then we just can't come up with a valid Chemkin name
    # for this species, so raise an exception
    raise ChemkinError("Unable to determine valid Chemkin identifier for species {0}.".format(species))

################################################################################

def writeThermoEntry(species, verbose = True):
    """
    Return a string representation of the NASA model readable by Chemkin.
    To use this method you must have exactly two NASA polynomials in your
    model, and you must use the seven-coefficient forms for each.
    """

    thermo = species.thermo
    if not isinstance(thermo, NASA):
        return ''
        raise ChemkinError('Cannot generate Chemkin string for species "{0}": Thermodynamics data must be a NASA object.'.format(species))

    assert len(thermo.polynomials) == 2
    assert thermo.polynomials[0].Tmin.value_si < thermo.polynomials[1].Tmin.value_si
    assert thermo.polynomials[0].Tmax.value_si == thermo.polynomials[1].Tmin.value_si
    assert thermo.polynomials[0].cm2 == 0 and thermo.polynomials[0].cm1 == 0
    assert thermo.polynomials[1].cm2 == 0 and thermo.polynomials[1].cm1 == 0

    # Determine the number of each type of element in the molecule
    elements = ['C','H','N','O']; elementCounts = [0,0,0,0]
    for atom in species.molecule[0].atoms:
        # The atom itself
        symbol = atom.element.symbol
        if symbol not in elements:
            elements.append(symbol)
            elementCounts.append(1)
        else:
            elementCounts[elements.index(symbol)] += 1
    # Remove elements with zero count
    index = 0
    while index < len(elementCounts):
        if elementCounts[index] == 0:
            del elements[index]
            del elementCounts[index]
        else:
            index += 1
    
    string = ''
    # Write thermo comments
    if verbose:
        if thermo.comment:
            string += '! Thermo comments:\n'
            for line in thermo.comment.split("\n"):
                string += "!   {0}\n".format(line) 

    # Line 1
    string += '{0:<16}        '.format(getSpeciesIdentifier(species))
    if len(elements) <= 4:
        # Use the original Chemkin syntax for the element counts
        for symbol, count in zip(elements, elementCounts):
            string += '{0!s:<2}{1:<3d}'.format(symbol, count)
        string += '     ' * (4 - len(elements))
    else:
        string += '     ' * 4
    string += 'G{0:<10.3f}{1:<10.3f}{2:<8.2f}      1'.format(thermo.polynomials[0].Tmin.value_si, thermo.polynomials[1].Tmax.value_si, thermo.polynomials[0].Tmax.value_si)
    if len(elements) > 4:
        string += '&\n'
        # Use the new-style Chemkin syntax for the element counts
        # This will only be recognized by Chemkin 4 or later
        for symbol, count in zip(elements, elementCounts):
            string += '{0!s:<2}{1:<3d}'.format(symbol, count)
    string += '\n'

    # Line 2
    string += '{0:< 15.8E}{1:< 15.8E}{2:< 15.8E}{3:< 15.8E}{4:< 15.8E}    2\n'.format(thermo.polynomials[1].c0, thermo.polynomials[1].c1, thermo.polynomials[1].c2, thermo.polynomials[1].c3, thermo.polynomials[1].c4)

    # Line 3
    string += '{0:< 15.8E}{1:< 15.8E}{2:< 15.8E}{3:< 15.8E}{4:< 15.8E}    3\n'.format(thermo.polynomials[1].c5, thermo.polynomials[1].c6, thermo.polynomials[0].c0, thermo.polynomials[0].c1, thermo.polynomials[0].c2)

    # Line 4
    string += '{0:< 15.8E}{1:< 15.8E}{2:< 15.8E}{3:< 15.8E}                   4\n'.format(thermo.polynomials[0].c3, thermo.polynomials[0].c4, thermo.polynomials[0].c5, thermo.polynomials[0].c6)

    return string

################################################################################

def writeKineticsEntry(reaction, speciesList, verbose = True, javaLibrary = False):
    """
    Return a string representation of the reaction as used in a Chemkin
    file. Use verbose = True to turn on comments.  Use javaLibrary = True in order to 
    generate a kinetics entry suitable for an RMG-Java kinetics library.  
    """
    string = ""
    
    if isinstance(reaction.kinetics, (MultiArrhenius, MultiPDepArrhenius)):
#        if isinstance(reaction,LibraryReaction):
#            string += '! Library reaction: {0!s}\n'.format(reaction.library.label)
        if verbose:
            if reaction.kinetics.comment:
                string += '! Kinetics comments:\n'
                for line in reaction.kinetics.comment.split("\n"):
                    string += "!   {0}\n".format(line) 
        for kinetics in reaction.kinetics.arrhenius:
            if isinstance(reaction,LibraryReaction):
                new_reaction = LibraryReaction( index=reaction.index,
                     reactants=reaction.reactants,
                     products=reaction.products,
                     reversible=reaction.reversible,
                     kinetics=kinetics,
                     library=reaction.library
                     )
            else:
                new_reaction = Reaction( index=reaction.index,
                         reactants=reaction.reactants,
                         products=reaction.products,
                         reversible=reaction.reversible,
                         kinetics=kinetics)
            string += writeKineticsEntry(new_reaction, speciesList, verbose, javaLibrary)
            string += "DUPLICATE\n"
        return string + "\n"
    
    if verbose:
        # First line of comment contains reaction equation
        string += '! {0!s}\n'.format(reaction)
        
        # Next line of comment contains Chemkin and RMG indices
        global __chemkin_reaction_count
        if __chemkin_reaction_count is not None:
            __chemkin_reaction_count += 1
            string += "! Reaction index: Chemkin #{0:d}; RMG #{1:d}\n".format(__chemkin_reaction_count, reaction.index)
        
        # Next line of comment contains information about the type of reaction
        if isinstance(reaction, TemplateReaction):
            string += '! Template reaction: {0!s} [{1!s}]\n'.format(reaction.family.label, ','.join([group.label for group in reaction.template]))
        elif isinstance(reaction, LibraryReaction):
            string += '! Library reaction: {0!s}\n'.format(reaction.library.label)
        elif isinstance(reaction, PDepReaction):
            string += '! PDep reaction: {0!s}\n'.format(reaction.network)
        
        # Next line of comment contains flux pairs
        if reaction.pairs is not None:
            string += '! Flux pairs: {0}\n'.format(
                '; '.join(['{0!s}, {1!s}'.format(getSpeciesIdentifier(reactant), getSpeciesIdentifier(product)) for reactant, product in reaction.pairs])
            )
    
        # Remaining lines of comments taken from reaction kinetics
        if reaction.kinetics.comment:
            string += '! Kinetics comments:\n'
            for line in reaction.kinetics.comment.split("\n"):
                string += "!   {0}\n".format(line)                               
    
    kinetics = reaction.kinetics
    numReactants = len(reaction.reactants)
    
    if javaLibrary:
        thirdBody = ''
        if kinetics.isPressureDependent():
            if isinstance(kinetics, ThirdBody) and not isinstance(kinetics, Lindemann) and not isinstance(kinetics, Troe):
                thirdBody = ' + M'
            elif isinstance(kinetics, PDepArrhenius):
                thirdBody = ''
            elif isinstance(kinetics, Chebyshev):
                thirdBody = ''
            else:
                thirdBody = ' (+M)'
        
        reaction_string = ' + '.join([getSpeciesIdentifier(reactant) for reactant in reaction.reactants])
        reaction_string += thirdBody
        reaction_string += ' => ' if not reaction.reversible else ' = '
        reaction_string += ' + '.join([getSpeciesIdentifier(product) for product in reaction.products])
        reaction_string += thirdBody
    
    else:
        thirdBody = ''
        if kinetics.isPressureDependent():
            if isinstance(kinetics, ThirdBody) and not isinstance(kinetics, Lindemann) and not isinstance(kinetics, Troe):
                thirdBody = '+M'
            elif isinstance(kinetics, PDepArrhenius):
                thirdBody = ''
            else:
                thirdBody = '(+M)'
        
        reaction_string = '+'.join([getSpeciesIdentifier(reactant) for reactant in reaction.reactants])
        reaction_string += thirdBody
        reaction_string += '=>' if not reaction.reversible else '='
        reaction_string += '+'.join([getSpeciesIdentifier(product) for product in reaction.products])
        reaction_string += thirdBody
    
    
    string += '{0!s:<51} '.format(reaction_string)

    if isinstance(kinetics, Arrhenius):
        string += '{0:<9.3e} {1:<9.3f} {2:<9.3f}'.format(
            kinetics.A.value_si/ (kinetics.T0.value_si ** kinetics.n.value_si) * 1.0e6 ** (numReactants - 1),
            kinetics.n.value_si,
            kinetics.Ea.value_si / 4184.
        )
    elif isinstance(kinetics, (Lindemann,Troe)):
        arrhenius = kinetics.arrheniusHigh
        string += '{0:<9.3e} {1:<9.3f} {2:<9.3f}'.format(
            arrhenius.A.value_si / (arrhenius.T0.value_si ** arrhenius.n.value_si) * 1.0e6 ** (numReactants - 1),
            arrhenius.n.value_si,
            arrhenius.Ea.value_si / 4184.
        )
    elif isinstance(kinetics, ThirdBody):
        arrhenius = kinetics.arrheniusLow
        string += '{0:<9.3e} {1:<9.3f} {2:<9.3f}'.format(
            arrhenius.A.value_si / (arrhenius.T0.value_si ** arrhenius.n.value_si) * 1.0e6 ** (numReactants),
            arrhenius.n.value_si,
            arrhenius.Ea.value_si / 4184.
        )
    elif hasattr(kinetics,'highPlimit') and kinetics.highPlimit is not None:
        arrhenius = kinetics.highPlimit
        string += '{0:<9.3e} {1:<9.3f} {2:<9.3f}'.format(
            arrhenius.A.value_si / (arrhenius.T0.value_si ** arrhenius.n.value_si) * 1.0e6 ** (numReactants - 1),
            arrhenius.n.value_si,
            arrhenius.Ea.value_si / 4184.
            )
    else:
        # Print dummy values that Chemkin parses but ignores
        string += '{0:<9.3e} {1:<9.3f} {2:<9.3f}'.format(1, 0, 0)
        
    if javaLibrary:
        # Assume uncertainties are zero (when parsing from chemkin), may need to adapt later
        string += '{0:<9.1f} {1:<9.1f} {2:<9.1f}'.format(0, 0, 0)

    string += '\n'

    if isinstance(kinetics, (ThirdBody, Lindemann, Troe)):
        # Write collider efficiencies
        for collider, efficiency in sorted(kinetics.efficiencies.items()):
            for species in speciesList:
                if any([collider.isIsomorphic(molecule) for molecule in species.molecule]):
                    string += '{0!s}/{1:<4.2f}/ '.format(getSpeciesIdentifier(species), efficiency)
                    break
        string += '\n'
        
        if isinstance(kinetics, (Lindemann, Troe)):
            # Write low-P kinetics
            arrhenius = kinetics.arrheniusLow
            string += '    LOW/ {0:<9.3e} {1:<9.3f} {2:<9.3f}/\n'.format(
                arrhenius.A.value_si / (arrhenius.T0.value_si ** arrhenius.n.value_si) * 1.0e6 ** (numReactants),
                arrhenius.n.value_si,
                arrhenius.Ea.value_si / 4184.
            )
            if isinstance(kinetics, Troe):
                # Write Troe parameters
                if kinetics.T2 is None:
                    string += '    TROE/ {0:<9.3e} {1:<9.3g} {2:<9.3g}/\n'.format(kinetics.alpha, kinetics.T3.value_si, kinetics.T1.value_si)
                else:
                    string += '    TROE/ {0:<9.3e} {1:<9.3g} {2:<9.3g} {3:<9.3g}/\n'.format(kinetics.alpha, kinetics.T3.value_si, kinetics.T1.value_si, kinetics.T2.value_si)
    elif isinstance(kinetics, PDepArrhenius):
        for P, arrhenius in zip(kinetics.pressures.value_si, kinetics.arrhenius):
            string += '    PLOG/ {0:<9.3f} {1:<9.3e} {2:<9.3f} {3:<9.3f}/\n'.format(P / 101325.,
                arrhenius.A.value_si / (arrhenius.T0.value_si ** arrhenius.n.value_si) * 1.0e6 ** (numReactants - 1),
                arrhenius.n.value_si,
                arrhenius.Ea.value_si / 4184.
            )
    elif isinstance(kinetics, Chebyshev):
        string += '    TCHEB/ {0:<9.3f} {1:<9.3f}/\n'.format(kinetics.Tmin.value_si, kinetics.Tmax.value_si)
        string += '    PCHEB/ {0:<9.3f} {1:<9.3f}/\n'.format(kinetics.Pmin.value_si / 101325., kinetics.Pmax.value_si / 101325.)
        string += '    CHEB/ {0:d} {1:d}/\n'.format(kinetics.degreeT, kinetics.degreeP)
        if kinetics.degreeP < 6:
            coeffs = kinetics.coeffs.value_si.copy()
            coeffs[0,0] += 6 * (numReactants - 1)
            for i in range(kinetics.degreeT):
                string += '    CHEB/'
                for j in range(kinetics.degreeP):
                    string += ' {0:<12.3e}'.format(coeffs[i,j])
                string += '/\n'
        else:
            coeffs = []
            for i in range(kinetics.degreeT):
                for j in range(kinetics.degreeP):
                    coeffs.append(kinetics.coeffs.value_si[i,j])
            coeffs[0] += 6 * (numReactants - 1)
            for i in range(len(coeffs)):
                if i % 5 == 0: string += '    CHEB/'
                string += ' {0:<12.3e}'.format(coeffs[i])
                if i % 5 == 4: string += '/\n'

    if reaction.duplicate:
        string += 'DUPLICATE\n'

    return string

################################################################################

def markDuplicateReactions(reactions):
    """
    For a given list of `reactions`, mark all of the duplicate reactions as
    understood by Chemkin.
    """
    for index1 in range(len(reactions)):
        reaction1 = reactions[index1]
        for index2 in range(index1+1, len(reactions)):
            reaction2 = reactions[index2]
            if reaction1.__class__ != reaction2.__class__:
                # TemplateReaction, LibraryReaction, and PDepReaction cannot be
                # duplicates of one another
                continue
            if reaction1.reactants == reaction2.reactants and reaction1.products == reaction2.products:
                if reaction1.duplicate and reaction2.duplicate:
                    continue
                else:
                    if reaction1.kinetics.isPressureDependent() == reaction2.kinetics.isPressureDependent():
                        # Only mark as duplicate if both reactions are pressure dependent or both are
                        # not pressure dependent.  Do not mark as duplicates otherwise.
                        logging.warning('Marked reaction {0} as duplicate for saving to Chemkin file.'.format(reaction1))
                        reaction1.duplicate = True
                        reaction2.duplicate = True     

def saveSpeciesDictionary(path, species):
    """
    Save the given list of `species` as adjacency lists in a text file `path` 
    on disk.
    """
    with open(path, 'w') as f:
        for spec in species:
            f.write(spec.molecule[0].toAdjacencyList(label=getSpeciesIdentifier(spec), removeH=True))
            f.write('\n')

def saveTransportFile(path, species):
    """
    Save a Chemkin transport properties file to `path` on disk containing the
    transport properties of the given list of `species`.
    """
    with open(path, 'w') as f:
        for spec in species:
            print spec.lennardJones
            if (not spec.lennardJones or not spec.dipoleMoment or
                not spec.polarizability or not spec.Zrot or 
                len(spec.molecule) == 0):
                continue
            
            label = getSpeciesIdentifier(spec)
            
            molecule = spec.molecule[0]
            if len(molecule.atoms) == 1:
                shapeIndex = 0
            elif molecule.isLinear():
                shapeIndex = 1
            else:
                shapeIndex = 2
            
            f.write('{0:19} {1:d} {2:9.3f} {3:9.3f} {4:9.3f} {5:9.3f} {6:9.3f}\n'.format(
                label,
                shapeIndex,
                spec.lennardJones.epsilon.value_si / constants.R,
                spec.lennardJones.sigma.value_si * 1e10,
                spec.dipoleMoment.value_si * constants.c * 1e21,
                spec.polarizability.value_si * 1e30,
                spec.Zrot.value_si,
            ))

def saveChemkinFile(path, species, reactions, verbose = True):
    """
    Save a Chemkin input file to `path` on disk containing the provided lists
    of `species` and `reactions`.
    """
    # Check for duplicate
    markDuplicateReactions(reactions)
    
    f = open(path, 'w')
    
    sorted_species = sorted(species, key=lambda species: species.index)

    # Elements section
    f.write('ELEMENTS H C O N Ne Ar He Si S END\n\n')

    # Species section
    f.write('SPECIES\n')
    for spec in sorted_species:
        label = getSpeciesIdentifier(spec)
        if verbose:
            f.write('    {0!s:<16}    ! {1}\n'.format(label, str(spec)))
        else:
            f.write('    {0!s:<16}\n'.format(label))
    f.write('END\n\n\n\n')

    # Thermodynamics section
    f.write('THERM ALL\n')
    f.write('    300.000  1000.000  5000.000\n\n')
    for spec in sorted_species:
        f.write(writeThermoEntry(spec, verbose=verbose))
        f.write('\n')
    f.write('END\n\n\n\n')

    ## Transport section would go here
    #f.write('TRANSPORT\n')
    #f.write('END\n\n')

    # Reactions section
    f.write('REACTIONS    KCAL/MOLE   MOLES\n\n')
    global __chemkin_reaction_count
    __chemkin_reaction_count = 0
    for rxn in reactions:
        f.write(writeKineticsEntry(rxn, speciesList=species, verbose=verbose))
        # Don't forget to mark duplicates!
        f.write('\n')
    f.write('END\n\n')
    f.close()
    logging.info("Chemkin file contains {0} reactions.".format(__chemkin_reaction_count))
    __chemkin_reaction_count = None

def saveJavaKineticsLibrary(path, species, reactions):
    """
    Save the reaction files for a RMG-Java kinetics library: pdepreactions.txt
    and reactions.txt given a list of reactions.
    """
    # Check for duplicate
    markDuplicateReactions(reactions)
    
    f = open(os.path.join(os.path.dirname(path), 'reactions.txt'), 'w')
    f2 = open(os.path.join(os.path.dirname(path), 'pdepreactions.txt'), 'w')

    # Headers
    f.write('Unit:\n')
    f.write('A: mol/cm3/s\n')
    f.write('E: kcal/mol\n')
    f.write('\n')
    f.write('Reactions:\n')
    f.write('\n')
    
    f2.write('Unit:\n')
    f2.write('A: mol/cm3/s\n')
    f2.write('E: kcal/mol\n')
    f2.write('\n')
    f2.write('Reactions:\n')
    f2.write('\n')
    

    for rxn in reactions:
        if rxn.kinetics.isPressureDependent():
            f2.write(writeKineticsEntry(rxn, speciesList=species, verbose = False, javaLibrary = True))
            f2.write('\n')
        else:  
            f.write(writeKineticsEntry(rxn, speciesList=species, verbose = False, javaLibrary = True))
            f.write('\n')
    f.close()
    f2.close()
