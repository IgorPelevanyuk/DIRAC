# $Header: /tmp/libdirac/tmp.stZoy15380/dirac/DIRAC3/DIRAC/Core/DISET/AuthManager.py,v 1.21 2008/06/11 10:25:38 acasajus Exp $
__RCSID__ = "$Id: AuthManager.py,v 1.21 2008/06/11 10:25:38 acasajus Exp $"

import types
from DIRAC.Core.Utilities.ReturnValues import S_OK, S_ERROR
from DIRAC.ConfigurationSystem.Client.Config import gConfig
from DIRAC.LoggingSystem.Client.Logger import gLogger
from DIRAC.Core.Security import CS

class AuthManager:

  __authLogger = gLogger.getSubLogger( "Authorization" )
  __hostsGroup = "hosts"

  def __init__( self, authSection ):
    """
    Constructor

    @type authSection: string
    @param authSection: Section containing the authorization rules
    """
    self.authSection = authSection

  def authQuery( self, methodQuery, credDict ):
    """
    Check if the query is authorized for a credentials dictionary

    @type  methodQuery: string
    @param methodQuery: Method to test
    @type  credDict: dictionary
    @param credDict: dictionary containing credentials for test. The dictionary can contain the DN
                        and selected group.
    @return: Boolean result of test
    """
    userString = ""
    if 'DN' in credDict:
      userString += "DN=%s" % credDict[ 'DN' ]
    if 'group' in credDict:
      userString += " group=%s" % credDict[ 'group' ]
    if 'extraCredentials' in credDict:
      userString += " extraCredentials=%s" % str( credDict[ 'extraCredentials' ] )
    self.__authLogger.warn( "Trying to authenticate %s" % userString )
    #Check if query comes though a gateway/web server
    if self.forwardedCredentials( credDict ):
      self.__authLogger.warn( "Query comes from a gateway" )
      self.unpackForwardedCredentials( credDict )
      return self.authQuery( methodQuery, credDict )
    #Check for invalid forwarding
    if 'extraCredentials' in credDict:
      #Invalid forwarding?
      if type( credDict[ 'extraCredentials' ] ) not in  ( types.StringType, types.UnicodeType ):
        self.__authLogger.warn( "The credentials seem to be forwarded by a host, but it is not a trusted one" )
        return False
    #Is it a host?
    if 'extraCredentials' in credDict and credDict[ 'extraCredentials' ] == self.__hostsGroup:
      #Get the nickname of the host
      credDict[ 'group' ] = credDict[ 'extraCredentials' ]
    #HACK TO MAINTAIN COMPATIBILITY
    else:
      if 'extraCredentials' in credDict and not 'group' in credDict:
        credDict[ 'group' ]  = credDict[ 'extraCredentials' ]
    #END OF HACK
    #Get the username
    if 'DN' in credDict:
      #For host
      if credDict[ 'group' ] == self.__hostsGroup:
        if not self.getHostNickName( credDict ):
          self.__authLogger.warn( "Host is invalid" )
          return False
      else:
      #For users
        if not self.getUsername( credDict ):
          self.__authLogger.warn( "User is invalid or does not belong to the group it's saying" )
          return False
    #Check everyone is authorized
    requiredProperties = self.getValidPropertiesForMethod( methodQuery )
    if "any" in requiredProperties or "all" in requiredProperties:
      return True
    #Check user is authenticated
    if not 'DN' in credDict:
      self.__authLogger.warn( "User has no DN" )
      return False
    #Check authorized groups
    if "authenticated" in requiredProperties:
      return True
    if not self.matchProperties( credDict[ 'groupProperties' ], requiredProperties ):
      self.__authLogger.warn( "Peer group is not authorized" )
      return False
    return True

  def getHostNickName( self, credDict ):
    """
    Discover the host nickname associated to the DN.
    The nickname will be included in the credentials dictionary.

    @type  credDict: dictionary
    @param credDict: Credentials to ckeck
    @return: Boolean specifying whether the nickname was found
    """
    if not "DN" in credDict:
      return True
    if not 'group' in credDict:
      return False
    retVal = CS.getHostnameForDN( credDict[ 'DN' ] )
    if not retVal[ 'OK' ]:
      gLogger.warn( "Cannot find hostname for DN %s: %s" % ( credDict[ 'DN' ], retVal[ 'Message' ] ) )
      return False
    credDict[ 'username' ] = retVal[ 'Value' ]
    return True

  def getValidPropertiesForMethod( self, method ):
    """
    Get all authorized groups for calling a method

    @type  method: string
    @param method: Method to test
    @return: List containing the allowed groups
    """
    authGroups = gConfig.getValue( "%s/%s" % ( self.authSection, method ), [] )
    if not authGroups:
      defaultPath = "%s/Default" % "/".join( method.split( "/" )[:-1] )
      self.__authLogger.warn( "Method %s has no properties defined, trying %s" % ( method, defaultPath ) )
      authGroups = gConfig.getValue( "%s/%s" % ( self.authSection, defaultPath ), [] )
    return authGroups

  def forwardedCredentials( self, credDict ):
    """
    Check whether the credentials are being forwarded by a valid source

    @type  credDict: dictionary
    @param credDict: Credentials to ckeck
    @return: Boolean with the result
    """
    trustedHostsList = CS.getTrustedHostList()
    return 'extraCredentials' in credDict and type( credDict[ 'extraCredentials' ] ) == types.TupleType and \
            'DN' in credDict and \
            credDict[ 'DN' ] in trustedHostsList

  def unpackForwardedCredentials( self, credDict ):
    """
    Extract the forwarded credentials

    @type  credDict: dictionary
    @param credDict: Credentials to unpack
    """
    credDict[ 'DN' ] = credDict[ 'extraCredentials' ][0]
    credDict[ 'group' ] = credDict[ 'extraCredentials' ][1]
    del( credDict[ 'extraCredentials' ] )


  def getUsername( self, credDict ):
    """
    Discover the username associated to the DN. It will check if the selected group is valid.
    The username will be included in the credentials dictionary.

    @type  credDict: dictionary
    @param credDict: Credentials to ckeck
    @return: Boolean specifying whether the username was found
    """
    if not "DN" in credDict:
      return True
    if not 'group' in credDict:
      credDict[ 'group' ] = CS.getDefaultUserGroup()
    credDict[ 'groupProperties' ] = CS.getPropertiesInGroup( credDict[ 'group' ], [])
    usersInGroup = CS.getUsersInGroup( credDict[ 'group' ], [] )
    if not usersInGroup:
      return False
    retVal = CS.getUsernameForDN( credDict[ 'DN' ], usersInGroup )
    if retVal[ 'OK' ]:
      credDict[ 'username' ] = retVal[ 'Value' ]
      return True
    return False

  def matchProperties( self, props, validProps ):
    """
    Return True if one or more properties are in the valid list of properties
    @type  props: list
    @param props: List of properties to match
    @type  validProps: list
    @param validProps: List of valid properties
    @return: Boolean specifying whether any property has matched the valid ones
    """
    for prop in props:
      if prop in validProps:
        return True
    return False