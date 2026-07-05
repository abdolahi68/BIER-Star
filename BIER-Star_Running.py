'''

Author : yunanhou

Date : 2023/11/11

Function : the second shortest path routing test cases at two locations under Constellation +Gird working mode

'''

import src.constellation_generation.by_XML.constellation_configuration as constellation_configuration
import src.XML_constellation.constellation_connectivity.connectivity_mode_plugin_manager as connectivity_mode_plugin_manager
import src.XML_constellation.constellation_routing.routing_policy_plugin_manager as routing_policy_plugin_manager
import src.XML_constellation.constellation_entity.user as USER

# the second shortest path routing test cases at two locations under Constellation +Gird working mode
def second_shortest_path():
    
    ######## Mostafa ############################
    # To enable using startlink you should uncomment the first line and comment the second line
    # i.e., dT = 5730  and constellation_name = "Starlink"
    # also, you can have more snapthots by chaning the values of dT
    # To enable using Oneweb you should uncomment the second line and comment the first line
    # i.e., dT = 6556 and constellation_name = "OneWeb" 
    # also, you can have more snapthots by chaning the values of dT
    
    #############################################
    # dT = 5730 # for starlink  5730 = 95mins that is the orbital time of starlink LEO satellites 
    dT = 1146 # for Oneweb 6556 = 109.27 mins that is the orbital time of Oneweb LEO satellites
    
    # constellation_name = "Starlink"
    constellation_name = "OneWeb"
    
    # the source of the communication pair
    source = USER.user(0.00, 51.30, "London")
    # source = USER.user(-122, 37, "California")

    # the target of the communication pair
    # target = USER.user(-74.00, 40.43, "NewYork")
    target = USER.user(-74.00, 40.43, "NewYork")

    # generate the constellations
    constellation = constellation_configuration.constellation_configuration(dT,
                                                                            constellation_name=constellation_name)
    
    
    # initialize the connectivity mode plugin manager
    connectionModePluginManager = connectivity_mode_plugin_manager.connectivity_mode_plugin_manager()
    # execute the connectivity mode and build ISLs between satellites
    connectionModePluginManager.execute_connection_policy(constellation=constellation, dT=dT)
    # initialize the routing policy plugin manager
    routingPolicyPluginManager = routing_policy_plugin_manager.routing_policy_plugin_manager()
    # switch the routing policy
    routingPolicyPluginManager.set_routing_policy("BIER_shortest_path")
    print("Source address = ")
    print(source.latitude)
    # execute the routing policy
    
    
    second_minimum_path = routingPolicyPluginManager.execute_routing_policy(constellation.constellation_name, source,
                                                                  target, constellation.shells[0])

    print("\t\t\tThe shortest path routing from ", source.user_name, " to ", target.user_name, " is " , second_minimum_path)



if __name__ == "__main__":
    second_shortest_path()