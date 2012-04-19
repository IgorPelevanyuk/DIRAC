import sys, time, threading, os
from DIRAC import gLogger
from DIRAC import S_OK, S_ERROR, gConfig, gLogger, gMonitor, rootPath
from DIRAC.ConfigurationSystem.Client import PathFinder
from DIRAC.FrameworkSystem.Client.MonitoringClient import MonitoringClient
from DIRAC.Core.Utilities.Shifter import setupShifterProxyInEnv
from DIRAC.Core.DISET.MessageClient import MessageClient
from DIRAC.Core.Utilities.ReturnValues import isReturnStructure

class ExecutorModule( object ):


  @classmethod
  def _ex_initialize( cls, exeName, loadName ):
    cls.__properties = { 'fullName' : exeName,
                         'loadName' : loadName,
                         'section' : PathFinder.getExecutorSection( exeName ),
                         'loadSection' : PathFinder.getExecutorSection( loadName ),
                         'messagesProcessed' : 0,
                         'reconnects' : 0,
                         'setup' : gConfig.getValue( "/DIRAC/Setup", "Unknown" ) }
    cls.__basePath = gConfig.getValue( '/LocalSite/InstancePath', rootPath )
    cls.__defaults = {}
    cls.__defaults[ 'MonitoringEnabled' ] = True
    cls.__defaults[ 'Enabled' ] = True
    cls.__defaults[ 'ControlDirectory' ] = os.path.join( cls.__basePath,
                                                          'control',
                                                          *exeName.split( "/" ) )
    cls.__defaults[ 'WorkDirectory' ] = os.path.join( cls.__basePath,
                                                       'work',
                                                       *exeName.split( "/" ) )
    cls.__defaults[ 'ReconnectRetries' ] = 10
    cls.__defaults[ 'ReconnectSleep' ] = 5
    cls.__properties[ 'shifterProxy' ] = ''
    cls.__properties[ 'shifterProxyLocation' ] = os.path.join( cls.__defaults[ 'WorkDirectory' ],
                                                               '.shifterCred' )
    cls.__mindName = False
    cls.__mindExtraArgs = False
    cls.log = gLogger.getSubLogger( exeName, child = False )

    try:
      result = cls.initialize()
    except Exception, excp:
      gLogger.exception( "Exception while initializing %s" % loadName )
      return S_ERROR( "Exception while initializing: %s" % str( excp ) )
    if not isReturnStructure( result ):
      return S_ERROR( "Executor %s does not resturn an S_OK/S_ERROR after initialization" % loadName )
    return result


  def __installShifterProxy( self ):
    shifterProxy = self.ex_getProperty( "shifterProxy" )
    if not shifterProxy:
      return S_OK()
    location = "%s-%s" % ( self.ex_getProperty( "shifterProxyLocation" ), shifterProxy )
    result = setupShifterProxyInEnv( shifterProxy, location )
    if not result[ 'OK' ]:
      self.log.error( "Cannot set shifter proxy: %s" % result[ 'Message' ] )
    return result

  @classmethod
  def ex_setOption( cls, optName, value ):
    cls.__defaults[ optName ] = value

  @classmethod
  def ex_getOption( cls, optName, defaultValue = None ):
    if defaultValue == None:
      if optName in cls.__defaults:
        defaultValue = cls.__defaults[ optName ]
    if optName and optName[0] == "/":
      return gConfig.getValue( optName, defaultValue )
    for section in ( cls.__properties[ 'section' ], cls.__properties[ 'loadSection' ] ):
      result = gConfig.getOption( "%s/%s" % ( section, optName ), defaultValue )
      if result[ 'OK' ]:
        return result[ 'Value' ]
    return defaultValue

  @classmethod
  def ex_setProperty( cls, optName, value ):
    cls.__properties[ optName ] = value

  @classmethod
  def ex_getProperty( cls, optName ):
    return cls.__properties[ optName ]

  @classmethod
  def ex_enabled( cls ):
    return cls.ex_getOption( "Enabled" )

  @classmethod
  def ex_setMind( cls, mindName, **extraArgs ):
    cls.__mindName = mindName
    cls.__mindExtraArgs = extraArgs

  @classmethod
  def ex_getMind( cls ):
    return cls.__mindName

  @classmethod
  def ex_getExtraArguments( cls ):
    return cls.__mindExtraArgs

  def __serialize( self, taskId, taskObj ):
    try:
      result = self.serializeTask( taskObj )
    except Exception, excp:
      gLogger.exception( "Exception while serializing task %s" % taskId )
      return S_ERROR( "Cannot serialize task %s: %s" % ( taskId, str( excp ) ) )
    if not isReturnStructure( result ):
      raise Exception( "serializeTask does not return a return structure" )
    return result

  def __deserialize( self, taskId, taskStub ):
    try:
      result = self.deserializeTask( taskStub )
    except Exception, excp:
      gLogger.exception( "Exception while deserializing task %s" % taskId  )
      return S_ERROR( "Cannot deserialize task %s: %s" % ( taskId, str( excp ) ) )
    if not isReturnStructure( result ):
      raise Exception( "deserializeTask does not return a return structure" )
    return result

  def _ex_processTask( self, taskId, taskStub ):
    self.__freezeTime = 0
    self.log.verbose( "Task %s: Received" % str( taskId ) )
    result = self.__deserialize( taskId, taskStub )
    if not result[ 'OK' ]:
      self.log.error( "Task %s: Cannot deserialize: %s" % ( str( taskId ), result[ 'Message' ] ) )
      return result
    taskObj = result[ 'Value' ]
    #Shifter proxy?
    result = self.__installShifterProxy()
    if not result[ 'OK' ]:
      return result
    #Execute!
    result = self.processTask( taskId, taskObj )
    if not isReturnStructure( result ):
      raise Exception( "processTask does not return a return structure" )
    if not result[ 'OK' ]:
      return result

    #If there's a result, serialize it again!
    if result[ 'Value' ]:
      taskObj = result[ 'Value' ]
    #Serialize again
    result = self.__serialize( taskId, taskObj )
    if not result[ 'OK' ]:
      self.log.verbose( "Task %s: Cannot serialize: %s" % ( str( taskId ), result[ 'Message' ] ) )
      return result
    taskStub = result[ 'Value' ]
    #EOP
    return S_OK( ( taskStub, self.__freezeTime ) )

  ####
  # Callable functions
  ####

  def freezeTask( self, freezeTime ):
    self.__freezeTime = freezeTime

  def isTaskFrozen( self ):
    return self.__freezeTime

  ####
  # Need to overwrite this functions
  ####

  def serializeTask( self, taskObj ):
    raise Exception( "Method serializeTask has to be coded!" )

  def deserializeTask( self, taskStub ):
    raise Exception( "Method deserializeTask has to be coded!" )

  def processTask( self, taskId, taskObj ):
    raise Exception( "Method processTask has to be coded!" )

